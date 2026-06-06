from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from models import SessionLocal, InspectionTask, Sample, Supplier
from notification import log_operation, push_alert, write_audit_log


def evaluate_result(
    measured_value: float,
    min_value: Optional[float],
    max_value: Optional[float],
    tolerance: Optional[float]
) -> Tuple[str, str]:
    if tolerance is not None:
        if min_value is not None:
            nominal = min_value
        elif max_value is not None:
            nominal = max_value
        else:
            nominal = measured_value
        lower = nominal * (1 - tolerance)
        upper = nominal * (1 + tolerance)
        if min_value is not None and max_value is not None:
            lower = min_value
            upper = max_value
            if tolerance:
                lower = lower - tolerance
                upper = upper + tolerance
    else:
        lower = min_value
        upper = max_value

    if lower is None and upper is None:
        return "合格", "无标准限制"

    if lower is not None and measured_value < lower:
        return "不合格", f"测量值 {measured_value} 低于下限 {lower}"
    if upper is not None and measured_value > upper:
        return "不合格", f"测量值 {measured_value} 高于上限 {upper}"
    return "合格", "符合标准要求"


def submit_inspection_result(
    task_code: str,
    result_value: float,
    inspected_by: str,
    remarks: Optional[str] = None,
    operator: str = "system"
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        task = db.query(InspectionTask).filter(
            InspectionTask.task_code == task_code
        ).first()
        if not task:
            return {"success": False, "error": f"任务编号 {task_code} 不存在"}
        if task.status in ["已完成", "合格", "不合格"]:
            return {"success": False, "error": f"任务 {task_code} 已完成，不可重复提交"}

        result_status, detail = evaluate_result(
            result_value,
            task.standard_min,
            task.standard_max,
            task.standard_tolerance
        )

        task.result_value = result_value
        task.result_status = result_status
        task.status = result_status
        task.inspected_at = datetime.now()
        task.inspected_by = inspected_by
        task.remarks = remarks

        sample = db.query(Sample).filter(Sample.id == task.sample_id).first()

        all_tasks = db.query(InspectionTask).filter(
            InspectionTask.sample_id == task.sample_id
        ).all()
        all_completed = all(
            t.status in ["合格", "不合格"] for t in all_tasks
        )
        all_pass = all(t.status == "合格" for t in all_tasks)
        any_fail = any(t.status == "不合格" for t in all_tasks)

        if all_completed:
            if all_pass:
                sample.status = "合格"
                overall = "合格"
            else:
                sample.status = "不合格"
                overall = "不合格"
        else:
            overall = "检测中"

        supplier = None
        if sample and all_completed:
            supplier = db.query(Supplier).filter(
                Supplier.id == sample.supplier_id
            ).first()
            if supplier:
                supplier.total_inspections += 1
                if all_pass:
                    supplier.pass_count += 1
                    supplier.consecutive_failures = 0
                else:
                    supplier.consecutive_failures += 1

        db.commit()

        log_operation(
            "检测结果录入",
            f"任务 {task_code} 结果:{result_status}，测量值:{result_value}"
            f"{task.unit or ''}，判定:{detail}",
            operator=operator or inspected_by,
            target_type="InspectionTask",
            target_id=task.id
        )
        write_audit_log(
            f"检测结果 | 任务:{task_code} | 项目:{task.inspection_item} | "
            f"测量值:{result_value}{task.unit or ''} | 结果:{result_status}"
        )

        result = {
            "success": True,
            "task_code": task_code,
            "inspection_item": task.inspection_item,
            "result_value": result_value,
            "unit": task.unit,
            "result_status": result_status,
            "evaluation_detail": detail,
            "sample_status": sample.status if sample else None,
            "overall": overall,
            "all_completed": all_completed,
            "sample_code": sample.sample_code if sample else None
        }

        if all_completed:
            failed_items = [
                f"{t.inspection_item}:{t.result_value}{t.unit or ''}"
                for t in all_tasks if t.status == "不合格"
            ]
            if any_fail:
                push_alert(
                    "⚠️ 样品检测不合格预警",
                    f"**样品编号**: {sample.sample_code}\n"
                    f"**供应商**: {supplier.supplier_name if supplier else '未知'}\n"
                    f"**产品名称**: {sample.product_name}\n"
                    f"**不合格项目**:\n" +
                    "\n".join([f"  - {fi}" for fi in failed_items]) +
                    f"\n**检测员**: {inspected_by}\n"
                    f"**将自动触发整改工单**",
                    level="warning"
                )
            else:
                push_alert(
                    "✅ 样品检测合格通知",
                    f"**样品编号**: {sample.sample_code}\n"
                    f"**供应商**: {supplier.supplier_name if supplier else '未知'}\n"
                    f"**产品名称**: {sample.product_name}\n"
                    f"**全部检测项目合格**",
                    level="info"
                )

        return result
    except Exception as e:
        db.rollback()
        log_operation(
            "检测结果录入-失败",
            f"任务 {task_code} 录入失败: {str(e)}",
            operator=operator
        )
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def get_sample_inspection_results(sample_code: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        sample = db.query(Sample).filter(
            Sample.sample_code == sample_code
        ).first()
        if not sample:
            return None

        tasks = db.query(InspectionTask).filter(
            InspectionTask.sample_id == sample.id
        ).all()

        task_results = []
        pass_count = 0
        fail_count = 0
        pending_count = 0

        for t in tasks:
            task_results.append({
                "task_code": t.task_code,
                "inspection_item": t.inspection_item,
                "laboratory": t.laboratory,
                "inspector": t.inspector,
                "standard": _format_standard(t),
                "result_value": t.result_value,
                "unit": t.unit,
                "status": t.status,
                "inspected_at": t.inspected_at.strftime("%Y-%m-%d %H:%M:%S") if t.inspected_at else None,
                "inspected_by": t.inspected_by
            })
            if t.status == "合格":
                pass_count += 1
            elif t.status == "不合格":
                fail_count += 1
            else:
                pending_count += 1

        supplier = db.query(Supplier).filter(
            Supplier.id == sample.supplier_id
        ).first()

        return {
            "sample_code": sample.sample_code,
            "supplier_name": supplier.supplier_name if supplier else None,
            "supplier_code": supplier.supplier_code if supplier else None,
            "sample_type": sample.sample_type,
            "product_name": sample.product_name,
            "overall_status": sample.status,
            "received_at": sample.received_at.strftime("%Y-%m-%d %H:%M:%S") if sample.received_at else None,
            "summary": {
                "total": len(tasks),
                "pass": pass_count,
                "fail": fail_count,
                "pending": pending_count
            },
            "tasks": task_results
        }
    finally:
        db.close()


def _format_standard(t: InspectionTask) -> str:
    parts = []
    if t.standard_min is not None and t.standard_max is not None:
        parts.append(f"{t.standard_min}~{t.standard_max}")
    elif t.standard_min is not None:
        parts.append(f"≥{t.standard_min}")
    elif t.standard_max is not None:
        parts.append(f"≤{t.standard_max}")
    if t.standard_tolerance:
        parts.append(f"±{t.standard_tolerance}")
    if t.unit:
        parts.append(t.unit)
    return " ".join(parts) if parts else "无标准"


def batch_submit_results(
    results: List[Dict[str, Any]],
    operator: str = "system"
) -> Dict[str, Any]:
    success_list = []
    failed_list = []
    for item in results:
        res = submit_inspection_result(
            task_code=item["task_code"],
            result_value=item["result_value"],
            inspected_by=item.get("inspected_by", operator),
            remarks=item.get("remarks"),
            operator=operator
        )
        if res.get("success"):
            success_list.append(res)
        else:
            failed_list.append({**item, "error": res.get("error")})
    return {
        "success": True,
        "total": len(results),
        "success_count": len(success_list),
        "failed_count": len(failed_list),
        "success_details": success_list,
        "failed_details": failed_list
    }
