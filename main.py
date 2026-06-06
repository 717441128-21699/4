import os
import sys
import json
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from models import init_db, SessionLocal, Sample, Supplier, InspectionTask
from sample_registration import register_sample, scan_and_register, get_sample_by_code
from task_assignment import assign_tasks_for_sample, auto_assign_pending_samples, get_task_by_code
from inspection import submit_inspection_result, get_sample_inspection_results, batch_submit_results
from rectification import (
    create_rectification_order, check_and_escalate_overdue,
    submit_rectification, verify_rectification,
    auto_create_rectification_for_failed, downgrade_supplier
)
from report_generator import generate_monthly_report, calculate_monthly_stats
from query_export import (
    query_samples, query_inspection_tasks, query_rectification_orders,
    export_to_excel
)
from notification import push_alert, log_operation, write_audit_log


class QualityManagementSystem:
    def __init__(self):
        init_db()
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._setup_scheduler()

    def _setup_scheduler(self):
        self.scheduler.add_job(
            self._monthly_report_job,
            trigger=CronTrigger(day=1, hour=8, minute=30, timezone="Asia/Shanghai"),
            id="monthly_report",
            name="每月1号生成质量月报",
            replace_existing=True
        )
        self.scheduler.add_job(
            self._escalation_check_job,
            trigger=IntervalTrigger(hours=1),
            id="escalation_check",
            name="每小时检查超期整改工单",
            replace_existing=True
        )
        self.scheduler.add_job(
            self._auto_rectification_job,
            trigger=IntervalTrigger(minutes=30),
            id="auto_rectification",
            name="每30分钟自动创建整改工单",
            replace_existing=True
        )

    def _monthly_report_job(self):
        now = datetime.now()
        last_month = now.replace(day=1) - timedelta(days=1)
        result = generate_monthly_report(
            year=last_month.year,
            month=last_month.month,
            operator="scheduler"
        )
        if result.get("success"):
            print(f"[定时任务] 月度报告生成成功: {result.get('period')}")
        else:
            print(f"[定时任务] 月度报告生成失败: {result.get('error')}")

    def _escalation_check_job(self):
        result = check_and_escalate_overdue(operator="scheduler")
        if result.get("escalated", 0) > 0:
            print(f"[定时任务] 升级 {result['escalated']} 个超期整改工单")

    def _auto_rectification_job(self):
        result = auto_create_rectification_for_failed(operator="scheduler")
        if result.get("new_created", 0) > 0:
            print(f"[定时任务] 自动创建 {result['new_created']} 个整改工单")

    def start_scheduler(self):
        if not self.scheduler.running:
            self.scheduler.start()
            print("[调度器] 已启动，定时任务运行中:")
            for job in self.scheduler.get_jobs():
                print(f"  - {job.name} (ID: {job.id})")

    def stop_scheduler(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            print("[调度器] 已停止")

    def register_sample(self, **kwargs) -> Dict[str, Any]:
        return register_sample(**kwargs)

    def scan_sample(self, qr_text: str, operator: str = "system") -> Dict[str, Any]:
        return scan_and_register(qr_text, operator=operator)

    def assign_tasks(self, sample_id: int, operator: str = "system",
                     custom_items: Optional[list] = None) -> Dict[str, Any]:
        return assign_tasks_for_sample(sample_id, operator=operator, custom_items=custom_items)

    def auto_assign_all(self, operator: str = "system") -> Dict[str, Any]:
        return auto_assign_pending_samples(operator=operator)

    def submit_result(self, task_code: str, result_value: float,
                      inspected_by: str, remarks: str = None,
                      operator: str = "system") -> Dict[str, Any]:
        return submit_inspection_result(task_code, result_value, inspected_by, remarks, operator)

    def batch_submit(self, results: list, operator: str = "system") -> Dict[str, Any]:
        return batch_submit_results(results, operator=operator)

    def create_rectification(self, sample_id: int, description: str = None,
                             operator: str = "system") -> Dict[str, Any]:
        return create_rectification_order(sample_id, description, operator)

    def submit_rectify(self, rectify_code: str, rectify_measures: str,
                       rectified_by: str, operator: str = "system") -> Dict[str, Any]:
        return submit_rectification(rectify_code, rectify_measures, rectified_by, operator)

    def verify_rectify(self, rectify_code: str, verified_by: str,
                       is_passed: bool = True, verify_remark: str = None,
                       operator: str = "system") -> Dict[str, Any]:
        return verify_rectification(rectify_code, verified_by, is_passed, verify_remark, operator)

    def check_escalation(self, operator: str = "system") -> Dict[str, Any]:
        return check_and_escalate_overdue(operator=operator)

    def generate_report(self, year: int = None, month: int = None,
                        operator: str = "system") -> Dict[str, Any]:
        return generate_monthly_report(year, month, operator)

    def query(self, query_type: str = "samples", **filters) -> Dict[str, Any]:
        if query_type == "samples":
            return query_samples(**filters)
        elif query_type == "inspection_tasks":
            return query_inspection_tasks(**filters)
        elif query_type == "rectification":
            return query_rectification_orders(**filters)
        else:
            return {"success": False, "error": f"未知查询类型: {query_type}"}

    def export(self, export_type: str = "samples", **filters) -> Dict[str, Any]:
        return export_to_excel(query_type=export_type, **filters)

    def get_sample_detail(self, sample_code: str) -> Optional[Dict[str, Any]]:
        return get_sample_by_code(sample_code)

    def get_task_detail(self, task_code: str) -> Optional[Dict[str, Any]]:
        return get_task_by_code(task_code)

    def get_sample_results(self, sample_code: str) -> Optional[Dict[str, Any]]:
        return get_sample_inspection_results(sample_code)

    def run_demo(self):
        print("\n" + "=" * 60)
        print("       供应商样品检测与质量管理系统 - 完整演示")
        print("=" * 60)

        sample_types = ["电子元器件", "金属材料", "塑料材料", "包装材料"]
        products = {
            "电子元器件": ["集成电路芯片", "贴片电阻", "电解电容", "连接器"],
            "金属材料": ["不锈钢板", "铝合金型材", "铜合金棒", "镀锌钢管"],
            "塑料材料": ["ABS颗粒", "PC工程塑料", "PVC管材", "PE薄膜"],
            "包装材料": ["纸箱", "泡沫衬垫", "标签贴纸", "收缩膜"]
        }

        db = SessionLocal()
        suppliers = db.query(Supplier).all()
        db.close()

        if not suppliers:
            print("❌ 未找到供应商数据，请先初始化数据库")
            return

        print(f"\n📋 步骤1: 样品登记 - 创建5个样品...")
        created_samples = []
        for i in range(5):
            supplier = random.choice(suppliers)
            s_type = random.choice(sample_types)
            product = random.choice(products[s_type])
            result = self.register_sample(
                supplier_code=supplier.supplier_code,
                sample_type=s_type,
                product_name=product,
                supplier_batch=f"BAT{datetime.now().strftime('%Y%m%d')}{i+1:03d}",
                product_spec=f"规格-{random.choice(['A','B','C'])}",
                quantity=random.randint(1, 50),
                received_by="质检部-李明",
                operator="demo_admin"
            )
            if result.get("success"):
                created_samples.append(result)
                print(f"  ✅ 样品 {result['sample_code']} - {supplier.supplier_name[:10]}... | {product}")
                print(f"     二维码路径: {result['qr_code_path']}")
            else:
                print(f"  ❌ 失败: {result.get('error')}")

        if not created_samples:
            print("❌ 样品登记失败，终止演示")
            return

        print(f"\n📋 步骤2: 自动分配检测任务...")
        for s in created_samples:
            detail = self.get_sample_detail(s["sample_code"])
            if detail:
                res = self.assign_tasks(detail["sample_id"], operator="demo_admin")
                if res.get("success"):
                    print(f"  ✅ 样品 {s['sample_code']} - {res['laboratory']} | {res['task_count']} 个任务")
                    for t in res["tasks"][:2]:
                        print(f"     · {t['inspection_item']} -> {t['inspector']} (标准:{t['standard']})")
                else:
                    print(f"  ❌ {s['sample_code']}: {res.get('error')}")

        print(f"\n📋 步骤3: 录入检测结果（部分合格，部分不合格）...")
        for s in created_samples:
            results = self.get_sample_results(s["sample_code"])
            if not results:
                continue
            for task in results["tasks"]:
                if task["status"] not in ["待检测", "检测中"]:
                    continue
                min_v = 50
                max_v = 100
                if task["inspection_item"] in ["外观检查", "密封性", "印刷质量", "金相分析"]:
                    measured = random.choice([0.0, 0.0, 1.0])
                elif task["inspection_item"] == "电气性能":
                    measured = round(random.uniform(88, 100), 2)
                elif task["inspection_item"] == "硬度测试":
                    measured = round(random.uniform(170, 230), 1)
                elif task["inspection_item"] == "拉伸强度":
                    measured = round(random.uniform(400, 500), 1)
                elif task["inspection_item"] == "熔融指数":
                    measured = round(random.uniform(7, 13), 2)
                elif task["inspection_item"] == "冲击强度":
                    measured = round(random.uniform(40, 65), 1)
                else:
                    measured = round(random.uniform(0, 100), 2)

                res = self.submit_result(
                    task_code=task["task_code"],
                    result_value=measured,
                    inspected_by=task["inspector"],
                    operator="demo_admin"
                )
                status_icon = "✅" if res.get("result_status") == "合格" else "❌"
                print(f"  {status_icon} {task['task_code']} - {task['inspection_item']}: "
                      f"{measured}{task.get('unit','')} -> {res.get('result_status')}")

        print(f"\n📋 步骤4: 自动为不合格样品创建整改工单...")
        rect_res = auto_create_rectification_for_failed(operator="demo_admin")
        print(f"  不合格样品: {rect_res.get('total_failed')} 个, 新建工单: {rect_res.get('new_created')} 个")
        for detail in rect_res.get("details", []):
            if detail.get("success"):
                level_alert = detail.get("supplier_level_alert")
                extra = ""
                if level_alert and level_alert.get("new_level"):
                    extra = f" | ⚠️ 供应商降级 {level_alert.get('old_level')}->{level_alert.get('new_level')}"
                print(f"  ✅ 工单 {detail['rectify_code']} | 截止:{detail['deadline'][:16]}{extra}")

        print(f"\n📋 步骤5: 检查超期整改工单（测试升级机制）...")
        esc_res = self.check_escalation(operator="demo_admin")
        print(f"  检查数量: {esc_res.get('checked')}, 升级: {esc_res.get('escalated')} 个")

        print(f"\n📋 步骤6: 生成月度质量报告...")
        now = datetime.now()
        rpt_res = self.generate_report(year=now.year, month=now.month, operator="demo_admin")
        if rpt_res.get("success"):
            print(f"  ✅ 报告周期: {rpt_res['period']}")
            print(f"  📄 PDF: {rpt_res['pdf_path']}")
            print(f"  📊 Excel: {rpt_res['excel_path']}")
            print(f"  📈 样品数:{rpt_res['stats']['total_samples']}, "
                  f"合格率:{rpt_res['stats']['pass_rate']}%, "
                  f"平均周期:{rpt_res['stats']['avg_cycle_days']}天")

        print(f"\n📋 步骤7: 多条件组合查询 & 批量导出...")
        q = self.query(query_type="samples", status="合格", page_size=5)
        print(f"  查询合格样品: 共 {q.get('total')} 条, 当前页 {len(q.get('data',[]))} 条")

        exp = self.export(export_type="all", operator="demo_admin")
        if exp.get("success"):
            print(f"  ✅ 完整数据导出: {exp['filepath']}")
            if isinstance(exp["record_count"], dict):
                print(f"     样品:{exp['record_count']['samples']}条, "
                      f"任务:{exp['record_count']['tasks']}条, "
                      f"工单:{exp['record_count']['rectifications']}条")

        print("\n" + "=" * 60)
        print("     ✅ 演示完成！所有功能均已成功运行")
        print("=" * 60 + "\n")


def print_menu():
    print("\n" + "=" * 50)
    print("   供应商样品检测与质量管理系统")
    print("=" * 50)
    print(" 1. 样品登记")
    print(" 2. 扫码登记")
    print(" 3. 自动分配检测任务")
    print(" 4. 录入检测结果")
    print(" 5. 查询样品/任务/工单")
    print(" 6. 批量导出数据")
    print(" 7. 生成月度质量报告")
    print(" 8. 检查超期整改工单")
    print(" 9. 运行完整演示")
    print(" 10. 启动定时调度器")
    print(" 11. 停止定时调度器")
    print(" 0. 退出")
    print("=" * 50)


def interactive_mode():
    qms = QualityManagementSystem()
    qms.start_scheduler()

    while True:
        print_menu()
        choice = input("请选择操作: ").strip()

        if choice == "0":
            qms.stop_scheduler()
            print("再见！")
            break
        elif choice == "1":
            print("\n=== 样品登记 ===")
            sc = input("供应商代码 (SUP001-SUP005): ").strip() or "SUP001"
            st = input("样品类型 (电子元器件/金属材料/塑料材料/包装材料): ").strip() or "电子元器件"
            pn = input("产品名称: ").strip() or "测试产品"
            sb = input("供应商批次 (可选): ").strip() or None
            qty = int(input("数量: ").strip() or "1")
            res = qms.register_sample(
                supplier_code=sc, sample_type=st, product_name=pn,
                supplier_batch=sb, quantity=qty, operator="admin"
            )
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif choice == "2":
            qr = input("输入二维码内容 (JSON或文本): ").strip()
            res = qms.scan_sample(qr, operator="admin")
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif choice == "3":
            sid = input("样品ID (留空则自动分配所有待检测): ").strip()
            if sid:
                res = qms.assign_tasks(int(sid), operator="admin")
            else:
                res = qms.auto_assign_all(operator="admin")
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif choice == "4":
            tc = input("任务编号: ").strip()
            rv = float(input("检测值: ").strip())
            ib = input("检测员: ").strip() or "system"
            res = qms.submit_result(tc, rv, ib, operator="admin")
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif choice == "5":
            print("查询类型: 1-样品 2-检测任务 3-整改工单")
            qt = input("选择: ").strip()
            mapping = {"1": "samples", "2": "inspection_tasks", "3": "rectification"}
            sn = input("供应商名称 (可选): ").strip() or None
            sd = input("开始日期 YYYY-MM-DD (可选): ").strip() or None
            ed = input("结束日期 YYYY-MM-DD (可选): ").strip() or None
            res = qms.query(query_type=mapping.get(qt, "samples"),
                            supplier_name=sn, start_date=sd, end_date=ed, page_size=20)
            print(f"共 {res.get('total')} 条记录:")
            for item in res.get("data", [])[:10]:
                print(f"  - {item}")
        elif choice == "6":
            print("导出类型: 1-样品 2-检测任务 3-整改工单 4-全部")
            et = input("选择: ").strip()
            mapping = {"1": "samples", "2": "inspection_tasks", "3": "rectification", "4": "all"}
            res = qms.export(export_type=mapping.get(et, "samples"), operator="admin")
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif choice == "7":
            y = input("年份 (默认今年): ").strip()
            m = input("月份 (默认本月): ").strip()
            res = qms.generate_report(
                year=int(y) if y else None,
                month=int(m) if m else None,
                operator="admin"
            )
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif choice == "8":
            res = qms.check_escalation(operator="admin")
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif choice == "9":
            qms.run_demo()
        elif choice == "10":
            qms.start_scheduler()
        elif choice == "11":
            qms.stop_scheduler()
        else:
            print("无效选择，请重试")


def main():
    if len(sys.argv) > 1:
        command = sys.argv[1]
        qms = QualityManagementSystem()
        if command == "demo":
            qms.run_demo()
        elif command == "report":
            y = int(sys.argv[2]) if len(sys.argv) > 2 else None
            m = int(sys.argv[3]) if len(sys.argv) > 3 else None
            res = qms.generate_report(year=y, month=m)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif command == "export":
            et = sys.argv[2] if len(sys.argv) > 2 else "all"
            res = qms.export(export_type=et)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif command == "daemon":
            qms.start_scheduler()
            print("后台调度运行中，按 Ctrl+C 停止...")
            try:
                import time
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                qms.stop_scheduler()
        else:
            print(f"未知命令: {command}")
            print("可用命令: demo, report [year] [month], export [type], daemon")
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
