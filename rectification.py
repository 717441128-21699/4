import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from config import (
    RECTIFY_CODE_PREFIX, RECTIFY_DEADLINE_HOURS,
    CONSECUTIVE_FAIL_THRESHOLD, ESCALATION_ROLE, SUPPLIER_LEVELS
)
from models import (
    SessionLocal, RectificationOrder, Sample, Supplier, InspectionTask
)
from notification import log_operation, push_alert, write_audit_log


def generate_rectify_code() -> str:
    now_str = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    short_uuid = uuid.uuid4().hex[:4].upper()
    return f"{RECTIFY_CODE_PREFIX}{now_str}{short_uuid}"


def create_rectification_order(
    sample_id: int,
    description: Optional[str] = None,
    operator: str = "system"
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        sample = db.query(Sample).filter(Sample.id == sample_id).first()
        if not sample:
            return {"success": False, "error": f"样品ID {sample_id} 不存在"}
        if sample.status != "不合格":
            return {
                "success": False,
                "error": f"样品 {sample.sample_code} 状态为 {sample.status}，仅不合格样品可创建整改工单"
            }

        existing = db.query(RectificationOrder).filter(
            RectificationOrder.sample_id == sample_id,
            RectificationOrder.status.in_(["待整改", "整改中", "已升级"])
        ).first()
        if existing:
            return {
                "success": False,
                "error": f"样品 {sample.sample_code} 已有未关闭的整改工单: {existing.rectify_code}"
            }

        failed_tasks = db.query(InspectionTask).filter(
            InspectionTask.sample_id == sample_id,
            InspectionTask.status == "不合格"
        ).all()

        failed_items_info = []
        primary_task_id = None
        for t in failed_tasks:
            standard_str = ""
            if t.standard_min is not None and t.standard_max is not None:
                standard_str = f"{t.standard_min}~{t.standard_max}"
            elif t.standard_min is not None:
                standard_str = f"≥{t.standard_min}"
            elif t.standard_max is not None:
                standard_str = f"≤{t.standard_max}"
            failed_items_info.append(
                f"{t.inspection_item}: 测得{t.result_value}{t.unit or ''}, "
                f"标准{standard_str}{t.unit or ''}"
            )
            if primary_task_id is None:
                primary_task_id = t.id

        failed_items_text = "\n".join(failed_items_info)
        deadline = datetime.now() + timedelta(hours=RECTIFY_DEADLINE_HOURS)

        rectify_code = generate_rectify_code()
        order = RectificationOrder(
            rectify_code=rectify_code,
            supplier_id=sample.supplier_id,
            sample_id=sample_id,
            task_id=primary_task_id,
            failed_items=failed_items_text,
            description=description or f"样品 {sample.sample_code} 检测不合格，需供应商整改",
            deadline=deadline,
            status="待整改"
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        supplier = db.query(Supplier).filter(
            Supplier.id == sample.supplier_id
        ).first()
        supplier_name = supplier.supplier_name if supplier else "未知"

        log_operation(
            "创建整改工单",
            f"工单 {rectify_code}，样品:{sample.sample_code}，"
            f"供应商:{supplier_name}，截止:{deadline.strftime('%Y-%m-%d %H:%M')}",
            operator=operator,
            target_type="RectificationOrder",
            target_id=order.id
        )
        write_audit_log(
            f"整改工单创建 | 工单:{rectify_code} | 样品:{sample.sample_code} | "
            f"供应商:{supplier_name} | 截止:{deadline.strftime('%Y-%m-%d %H:%M')}"
        )

        push_alert(
            "🚨 整改工单创建通知",
            f"**工单编号**: {rectify_code}\n"
            f"**样品编号**: {sample.sample_code}\n"
            f"**供应商**: {supplier_name}\n"
            f"**产品名称**: {sample.product_name}\n"
            f"**不合格项**:\n{failed_items_text}\n"
            f"**整改截止**: {deadline.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**说明**: 请在 {RECTIFY_DEADLINE_HOURS} 小时内完成整改，逾期将自动升级",
            level="warning"
        )

        level_alert = None
        if supplier and supplier.consecutive_failures >= CONSECUTIVE_FAIL_THRESHOLD:
            level_alert = downgrade_supplier(supplier.id, operator=operator)

        return {
            "success": True,
            "rectify_code": rectify_code,
            "order_id": order.id,
            "sample_code": sample.sample_code,
            "supplier_name": supplier_name,
            "deadline": deadline.strftime("%Y-%m-%d %H:%M:%S"),
            "failed_items": failed_items_info,
            "supplier_level_alert": level_alert
        }
    except Exception as e:
        db.rollback()
        log_operation(
            "创建整改工单-失败",
            f"样品ID {sample_id} 创建失败: {str(e)}",
            operator=operator
        )
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def downgrade_supplier(supplier_id: int, operator: str = "system") -> Dict[str, Any]:
    db = SessionLocal()
    try:
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            return {"success": False, "error": f"供应商ID {supplier_id} 不存在"}

        current_level = supplier.level
        level_order = ["A", "B", "C", "D"]
        if current_level not in level_order:
            current_idx = 1
        else:
            current_idx = level_order.index(current_level)

        new_idx = min(current_idx + 1, len(level_order) - 1)
        new_level = level_order[new_idx]

        if new_level == current_level:
            return {
                "success": True,
                "message": f"供应商已处于最低等级 {current_level}",
                "level": current_level
            }

        supplier.level = new_level
        supplier.consecutive_failures = 0
        db.commit()

        level_desc = SUPPLIER_LEVELS.get(new_level, {}).get("description", "")

        log_operation(
            "供应商等级降级",
            f"供应商 {supplier.supplier_name} 从 {current_level} 降级至 {new_level}，"
            f"原因:连续 {CONSECUTIVE_FAIL_THRESHOLD} 次不合格",
            operator=operator,
            target_type="Supplier",
            target_id=supplier.id
        )
        write_audit_log(
            f"供应商降级 | {supplier.supplier_name} | {current_level} -> {new_level} | "
            f"连续不合格次数:{CONSECUTIVE_FAIL_THRESHOLD}"
        )

        push_alert(
            "🔴 供应商等级降级通知",
            f"**供应商**: {supplier.supplier_name} ({supplier.supplier_code})\n"
            f"**等级变更**: {current_level} → {new_level}\n"
            f"**新等级说明**: {level_desc}\n"
            f"**降级原因**: 连续 {CONSECUTIVE_FAIL_THRESHOLD} 次送检不合格\n"
            f"**采购部门请注意**: 请根据新等级调整采购策略和抽检比例",
            level="error"
        )

        return {
            "success": True,
            "supplier_code": supplier.supplier_code,
            "supplier_name": supplier.supplier_name,
            "old_level": current_level,
            "new_level": new_level,
            "level_description": level_desc
        }
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def check_and_escalate_overdue(operator: str = "system") -> Dict[str, Any]:
    db = SessionLocal()
    try:
        now = datetime.now()
        overdue_orders = db.query(RectificationOrder).filter(
            RectificationOrder.status.in_(["待整改", "整改中"]),
            RectificationOrder.deadline < now,
            RectificationOrder.is_escalated == False
        ).all()

        escalated = []
        for order in overdue_orders:
            order.is_escalated = True
            order.escalated_at = now
            order.status = "已升级"

            sample = db.query(Sample).filter(Sample.id == order.sample_id).first()
            supplier = db.query(Supplier).filter(Supplier.id == order.supplier_id).first()

            log_operation(
                "整改工单升级",
                f"工单 {order.rectify_code} 已超过 {RECTIFY_DEADLINE_HOURS} 小时未处理，"
                f"升级至 {ESCALATION_ROLE}",
                operator=operator,
                target_type="RectificationOrder",
                target_id=order.id
            )
            write_audit_log(
                f"整改升级 | 工单:{order.rectify_code} | 升级至:{ESCALATION_ROLE}"
            )

            push_alert(
                "🚨 整改工单超期升级通知",
                f"**工单编号**: {order.rectify_code}\n"
                f"**样品编号**: {sample.sample_code if sample else '未知'}\n"
                f"**供应商**: {supplier.supplier_name if supplier else '未知'}\n"
                f"**整改截止**: {order.deadline.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"**超期时长**: {(now - order.deadline).total_seconds() / 3600:.1f} 小时\n"
                f"**升级至**: {ESCALATION_ROLE}\n"
                f"**不合格项**:\n{order.failed_items}",
                level="urgent"
            )

            escalated.append({
                "rectify_code": order.rectify_code,
                "supplier": supplier.supplier_name if supplier else "未知",
                "overdue_hours": round((now - order.deadline).total_seconds() / 3600, 1)
            })

        db.commit()
        return {
            "success": True,
            "checked": len(overdue_orders),
            "escalated": len(escalated),
            "details": escalated
        }
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def submit_rectification(
    rectify_code: str,
    rectify_measures: str,
    rectified_by: str,
    operator: str = "system"
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        order = db.query(RectificationOrder).filter(
            RectificationOrder.rectify_code == rectify_code
        ).first()
        if not order:
            return {"success": False, "error": f"工单 {rectify_code} 不存在"}

        order.rectify_measures = rectify_measures
        order.rectified_at = datetime.now()
        order.status = "待验证"

        supplier = db.query(Supplier).filter(
            Supplier.id == order.supplier_id
        ).first()

        db.commit()

        log_operation(
            "提交整改措施",
            f"工单 {rectify_code} 由 {rectified_by} 提交整改",
            operator=operator or rectified_by,
            target_type="RectificationOrder",
            target_id=order.id
        )

        push_alert(
            "整改措施已提交",
            f"**工单编号**: {rectify_code}\n"
            f"**供应商**: {supplier.supplier_name if supplier else '未知'}\n"
            f"**整改措施**: {rectify_measures}\n"
            f"**提交人**: {rectified_by}\n"
            f"**状态**: 待质量部门验证",
            level="info"
        )

        return {
            "success": True,
            "rectify_code": rectify_code,
            "status": "待验证"
        }
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def verify_rectification(
    rectify_code: str,
    verified_by: str,
    is_passed: bool = True,
    verify_remark: Optional[str] = None,
    operator: str = "system"
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        order = db.query(RectificationOrder).filter(
            RectificationOrder.rectify_code == rectify_code
        ).first()
        if not order:
            return {"success": False, "error": f"工单 {rectify_code} 不存在"}
        if order.status != "待验证":
            return {"success": False, "error": f"工单状态为 {order.status}，不可验证"}

        order.verified_by = verified_by
        order.verified_at = datetime.now()
        order.status = "已完成" if is_passed else "整改驳回"

        supplier = db.query(Supplier).filter(
            Supplier.id == order.supplier_id
        ).first()

        db.commit()

        log_operation(
            "整改验证",
            f"工单 {rectify_code} 验证{'通过' if is_passed else '不通过'}，验证人:{verified_by}",
            operator=operator or verified_by,
            target_type="RectificationOrder",
            target_id=order.id
        )

        push_alert(
            f"整改验证{'通过' if is_passed else '不通过'}",
            f"**工单编号**: {rectify_code}\n"
            f"**供应商**: {supplier.supplier_name if supplier else '未知'}\n"
            f"**验证结果**: {'✅ 通过' if is_passed else '❌ 不通过'}\n"
            f"**验证人**: {verified_by}\n"
            f"**备注**: {verify_remark or '无'}",
            level="info" if is_passed else "warning"
        )

        return {
            "success": True,
            "rectify_code": rectify_code,
            "status": order.status,
            "verified_by": verified_by
        }
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def auto_create_rectification_for_failed(operator: str = "system") -> Dict[str, Any]:
    db = SessionLocal()
    try:
        failed_samples = db.query(Sample).filter(Sample.status == "不合格").all()
        created = []
        for sample in failed_samples:
            existing = db.query(RectificationOrder).filter(
                RectificationOrder.sample_id == sample.id
            ).first()
            if not existing:
                res = create_rectification_order(sample.id, operator=operator)
                if res.get("success"):
                    created.append(res)
        return {
            "success": True,
            "total_failed": len(failed_samples),
            "new_created": len(created),
            "details": created
        }
    finally:
        db.close()
