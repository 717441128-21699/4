import uuid

from datetime import datetime
from typing import List, Dict, Any, Optional

from config import (
    TASK_CODE_PREFIX, INSPECTION_STANDARDS,
    LABORATORIES, INSPECTORS
)
from models import SessionLocal, Sample, InspectionTask, InspectionStandard
from notification import log_operation, push_alert, write_audit_log


def generate_task_code() -> str:
    now_str = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    short_uuid = uuid.uuid4().hex[:4].upper()
    return f"{TASK_CODE_PREFIX}{now_str}{short_uuid}"


def select_laboratory(sample_type: str) -> str:
    for lab, types in LABORATORIES.items():
        if sample_type in types and lab != "综合实验室":
            return lab
    return "综合实验室"


def select_inspector(laboratory: str) -> str:
    db = SessionLocal()
    try:
        inspectors = INSPECTORS.get(laboratory, [])
        if not inspectors:
            return "未分配"
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        workload = {}
        for insp in inspectors:
            count = db.query(InspectionTask).filter(
                InspectionTask.inspector == insp,
                InspectionTask.laboratory == laboratory,
                InspectionTask.created_at >= today_start,
                InspectionTask.status.in_(["待检测", "检测中"])
            ).count()
            workload[insp] = count
        return min(workload, key=workload.get)
    finally:
        db.close()


def get_standards_for_type(sample_type: str) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        standards = db.query(InspectionStandard).filter(
            InspectionStandard.sample_type == sample_type
        ).all()
        result = []
        for std in standards:
            result.append({
                "inspection_item": std.inspection_item,
                "min_value": std.min_value,
                "max_value": std.max_value,
                "tolerance": std.tolerance,
                "unit": std.unit,
                "method": std.method
            })
        if not result and sample_type in INSPECTION_STANDARDS:
            for item_name, spec in INSPECTION_STANDARDS[sample_type].items():
                result.append({
                    "inspection_item": item_name,
                    "min_value": spec.get("min"),
                    "max_value": spec.get("max"),
                    "tolerance": spec.get("tolerance"),
                    "unit": spec.get("unit"),
                    "method": spec.get("method")
                })
        return result
    finally:
        db.close()


def assign_tasks_for_sample(
    sample_id: int,
    operator: str = "system",
    custom_items: Optional[List[str]] = None
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        sample = db.query(Sample).filter(Sample.id == sample_id).first()
        if not sample:
            return {"success": False, "error": f"样品ID {sample_id} 不存在"}

        existing = db.query(InspectionTask).filter(
            InspectionTask.sample_id == sample_id
        ).first()
        if existing:
            return {
                "success": False,
                "error": f"样品 {sample.sample_code} 已分配检测任务"
            }

        standards = get_standards_for_type(sample.sample_type)
        if not standards:
            return {
                "success": False,
                "error": f"样品类型 {sample.sample_type} 无检测标准"
            }

        if custom_items:
            standards = [s for s in standards if s["inspection_item"] in custom_items]
            if not standards:
                return {"success": False, "error": "指定的检测项目均无对应标准"}

        laboratory = select_laboratory(sample.sample_type)
        created_tasks = []

        for std in standards:
            task_code = generate_task_code()
            inspector = select_inspector(laboratory)

            task = InspectionTask(
                task_code=task_code,
                sample_id=sample.id,
                inspection_item=std["inspection_item"],
                laboratory=laboratory,
                inspector=inspector,
                standard_min=std["min_value"],
                standard_max=std["max_value"],
                standard_tolerance=std["tolerance"],
                unit=std["unit"],
                method=std["method"],
                status="待检测"
            )
            db.add(task)
            db.flush()
            created_tasks.append({
                "task_id": task.id,
                "task_code": task_code,
                "inspection_item": std["inspection_item"],
                "laboratory": laboratory,
                "inspector": inspector,
                "standard": f"{std['min_value'] if std['min_value'] is not None else '-'}~"
                           f"{std['max_value'] if std['max_value'] is not None else '-'}"
                           f"{f'±{std["tolerance"]}' if std['tolerance'] else ''} {std['unit'] or ''}"
            })

        sample.status = "检测中"
        db.commit()

        log_operation(
            "任务分配",
            f"样品 {sample.sample_code} 分配 {len(created_tasks)} 个检测任务，"
            f"实验室:{laboratory}",
            operator=operator,
            target_type="Sample",
            target_id=sample.id
        )
        write_audit_log(
            f"任务分配 | 样品:{sample.sample_code} | 实验室:{laboratory} | "
            f"任务数:{len(created_tasks)}"
        )

        tasks_summary = "\n".join([
            f"  - {t['inspection_item']} | 检测员:{t['inspector']} | 标准:{t['standard']}"
            for t in created_tasks
        ])
        push_alert(
            "检测任务分配通知",
            f"**样品编号**: {sample.sample_code}\n"
            f"**样品类型**: {sample.sample_type}\n"
            f"**产品名称**: {sample.product_name}\n"
            f"**检测实验室**: {laboratory}\n"
            f"**分配任务**:\n{tasks_summary}",
            level="info"
        )

        return {
            "success": True,
            "sample_code": sample.sample_code,
            "laboratory": laboratory,
            "tasks": created_tasks,
            "task_count": len(created_tasks)
        }
    except Exception as e:
        db.rollback()
        log_operation(
            "任务分配-失败",
            f"样品ID {sample_id} 分配失败: {str(e)}",
            operator=operator
        )
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def auto_assign_pending_samples(operator: str = "system") -> Dict[str, Any]:
    db = SessionLocal()
    try:
        pending = db.query(Sample).filter(Sample.status == "待检测").all()
        results = []
        for sample in pending:
            res = assign_tasks_for_sample(sample.id, operator=operator)
            results.append(res)
        success_count = sum(1 for r in results if r.get("success"))
        return {
            "success": True,
            "total": len(pending),
            "assigned": success_count,
            "failed": len(pending) - success_count,
            "details": results
        }
    finally:
        db.close()


def get_task_by_code(task_code: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        task = db.query(InspectionTask).filter(
            InspectionTask.task_code == task_code
        ).first()
        if not task:
            return None
        return {
            "task_id": task.id,
            "task_code": task.task_code,
            "sample_code": task.sample.sample_code if task.sample else None,
            "inspection_item": task.inspection_item,
            "laboratory": task.laboratory,
            "inspector": task.inspector,
            "standard_min": task.standard_min,
            "standard_max": task.standard_max,
            "standard_tolerance": task.standard_tolerance,
            "unit": task.unit,
            "method": task.method,
            "status": task.status,
            "result_value": task.result_value,
            "result_status": task.result_status
        }
    finally:
        db.close()
