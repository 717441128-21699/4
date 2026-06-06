import os
import sys
import json
from datetime import datetime

from models import init_db, SessionLocal, Sample, Supplier, InspectionTask
from sample_registration import register_sample, get_sample_by_code
from task_assignment import assign_tasks_for_sample, auto_assign_pending_samples
from inspection import submit_inspection_result, get_sample_inspection_results
from rectification import create_rectification_order, check_and_escalate_overdue, auto_create_rectification_for_failed
from report_generator import generate_monthly_report
from query_export import query_samples, query_inspection_tasks, query_rectification_orders, export_to_excel
from notification import log_operation


def init_system():
    print("正在初始化系统...")
    init_db()
    db = SessionLocal()
    try:
        sup_count = db.query(Supplier).count()
        std_count = db.query(InspectionTask).count()  # just check tables work
        print(f"  数据库初始化完成，已加载 {sup_count} 家供应商")
    finally:
        db.close()


def print_menu():
    print("\n" + "=" * 60)
    print("      供应商样品检测与质量管理系统")
    print("=" * 60)
    print("  1. 登记样品")
    print("  2. 录入检测结果")
    print("  3. 查询历史记录")
    print("  4. 生成月度质量报告")
    print("  5. 批量导出数据")
    print("  6. 运行完整演示（2个样品从登记到出报告）")
    print("  0. 退出")
    print("=" * 60)


def action_register_sample():
    print("\n--- 样品登记 ---")
    db = SessionLocal()
    try:
        suppliers = db.query(Supplier).all()
        print("可选供应商：")
        for s in suppliers:
            print(f"  [{s.supplier_code}] {s.supplier_name} (等级:{s.level})")
    finally:
        db.close()

    supplier_code = input("请输入供应商代码 (默认SUP001): ").strip() or "SUP001"
    print("\n样品类型: 电子元器件 / 金属材料 / 塑料材料 / 包装材料")
    sample_type = input("请输入样品类型 (默认电子元器件): ").strip() or "电子元器件"
    product_name = input("请输入产品名称: ").strip() or "测试产品"
    supplier_batch = input("请输入供应商批次 (可选): ").strip() or None
    qty_input = input("请输入数量 (默认1): ").strip()
    quantity = int(qty_input) if qty_input.isdigit() else 1

    res = register_sample(
        supplier_code=supplier_code,
        sample_type=sample_type,
        product_name=product_name,
        supplier_batch=supplier_batch,
        quantity=quantity,
        received_by="质检部",
        operator="admin"
    )

    if res.get("success"):
        print(f"\n✅ 登记成功！")
        print(f"   样品编号: {res['sample_code']}")
        print(f"   供应商:   {res['supplier_name']}")
        print(f"   二维码:   {res['qr_code_path']}")

        do_assign = input("\n是否立即分配检测任务? (y/n, 默认y): ").strip().lower()
        if do_assign in ("", "y", "yes"):
            assign_res = assign_tasks_for_sample(res["sample_id"], operator="admin")
            if assign_res.get("success"):
                print(f"\n✅ 任务分配成功，共 {assign_res['task_count']} 个检测任务")
                print(f"   实验室: {assign_res['laboratory']}")
                for t in assign_res["tasks"]:
                    print(f"     - {t['inspection_item']:8s} | 检测员:{t['inspector']:6s} | 标准:{t['standard']}")
    else:
        print(f"\n❌ 登记失败: {res.get('error')}")


def action_submit_result():
    print("\n--- 录入检测结果 ---")
    auto_assign_pending_samples(operator="admin")

    db = SessionLocal()
    try:
        pending = db.query(InspectionTask).filter(
            InspectionTask.status.in_(["待检测", "检测中"])
        ).all()
        if not pending:
            print("没有待检测的任务，请先登记样品。")
            return
        print(f"共找到 {len(pending)} 个待检测任务：")
        for t in pending:
            print(f"  [{t.task_code}] 样品:{t.sample.sample_code} | 项目:{t.inspection_item} | 检测员:{t.inspector}")
    finally:
        db.close()

    task_code = input("\n请输入任务编号: ").strip()
    if not task_code:
        print("已取消")
        return
    value_input = input("请输入检测值 (数字): ").strip()
    if not value_input:
        print("已取消")
        return
    try:
        result_value = float(value_input)
    except ValueError:
        print("❌ 请输入有效数字")
        return

    inspected_by = input("请输入检测员姓名 (默认system): ").strip() or "system"
    res = submit_inspection_result(task_code, result_value, inspected_by, operator="admin")

    if res.get("success"):
        icon = "✅" if res.get("result_status") == "合格" else "❌"
        print(f"\n{icon} 结果: {res.get('result_status')}")
        print(f"   测量值: {res.get('result_value')}{res.get('unit', '')}")
        print(f"   判定说明: {res.get('evaluation_detail')}")
        print(f"   样品当前状态: {res.get('sample_status')}")

        if res.get("all_completed") and res.get("overall") == "不合格":
            do_rect = input("\n检测发现不合格，是否创建整改工单? (y/n, 默认y): ").strip().lower()
            if do_rect in ("", "y", "yes"):
                db = SessionLocal()
                try:
                    sample = db.query(Sample).filter(Sample.sample_code == res.get("sample_code")).first()
                    if sample:
                        rr = create_rectification_order(sample.id, operator="admin")
                        if rr.get("success"):
                            print(f"\n✅ 整改工单已创建: {rr['rectify_code']}")
                            print(f"   截止时间: {rr['deadline']}")
                            if rr.get("supplier_level_alert"):
                                la = rr["supplier_level_alert"]
                                if la.get("new_level"):
                                    print(f"   ⚠️ 供应商等级变更: {la.get('old_level')} -> {la.get('new_level')}")
                finally:
                    db.close()
    else:
        print(f"\n❌ 提交失败: {res.get('error')}")


def action_query():
    print("\n--- 查询历史记录 ---")
    print("  1. 查询样品记录")
    print("  2. 查询检测任务")
    print("  3. 查询整改工单")
    qt_choice = input("请选择查询类型 (默认1): ").strip() or "1"

    supplier_name = input("供应商名称 (可留空): ").strip() or None
    start_date = input("开始日期 YYYY-MM-DD (可留空): ").strip() or None
    end_date = input("结束日期 YYYY-MM-DD (可留空): ").strip() or None

    type_map = {"1": "samples", "2": "inspection_tasks", "3": "rectification"}
    query_type = type_map.get(qt_choice, "samples")

    if query_type == "samples":
        res = query_samples(supplier_name=supplier_name, start_date=start_date, end_date=end_date, page_size=50)
        print(f"\n共找到 {res.get('total')} 条样品记录：")
        for s in res.get("data", []):
            print(f"  [{s['sample_code']}] {s['supplier_name']:20s} | {s['sample_type']:8s} | "
                  f"{s['product_name']:15s} | 状态:{s['status']:6s} | {s['received_at']}")
    elif query_type == "inspection_tasks":
        res = query_inspection_tasks(supplier_name=supplier_name, start_date=start_date, end_date=end_date, page_size=50)
        print(f"\n共找到 {res.get('total')} 条检测任务：")
        for t in res.get("data", []):
            print(f"  [{t['task_code']}] 样品:{t['sample_code']:15s} | {t['inspection_item']:8s} | "
                  f"检测员:{t['inspector']:6s} | 结果:{t.get('result_status') or t['status']:6s} | "
                  f"值:{t.get('result_value', '-')}{t.get('unit','')}")
    else:
        res = query_rectification_orders(supplier_name=supplier_name, start_date=start_date, end_date=end_date, page_size=50)
        print(f"\n共找到 {res.get('total')} 条整改工单：")
        for r in res.get("data", []):
            print(f"  [{r['rectify_code']}] {r['supplier_name']:20s} | 样品:{r['sample_code']:15s} | "
                  f"状态:{r['status']:8s} | 截止:{r['deadline']}")


def action_generate_report():
    print("\n--- 生成月度质量报告 ---")
    now = datetime.now()
    y_input = input(f"年份 (默认 {now.year}): ").strip()
    m_input = input(f"月份 (默认 {now.month}): ").strip()
    year = int(y_input) if y_input.isdigit() else now.year
    month = int(m_input) if m_input.isdigit() else now.month

    print(f"正在生成 {year}年{month}月 报告...")
    res = generate_monthly_report(year=year, month=month, operator="admin")

    if res.get("success"):
        print("\n✅ 报告生成成功！")
        print(f"   📄 PDF 报告: {res['pdf_path']}")
        print(f"   📊 Excel 报告: {res['excel_path']}")
        print(f"   📈 本月样品数: {res['stats']['total_samples']}")
        print(f"   📈 整体合格率: {res['stats']['pass_rate']}%")
        print(f"   📈 平均检测周期: {res['stats']['avg_cycle_days']} 天")
        print(f"   📈 参与供应商: {res['stats']['supplier_count']} 家")
    else:
        print(f"\n❌ 报告生成失败: {res.get('error')}")


def action_export():
    print("\n--- 批量导出数据 ---")
    print("  1. 导出样品记录")
    print("  2. 导出检测任务")
    print("  3. 导出整改工单")
    print("  4. 导出全部数据（推荐）")
    choice = input("请选择 (默认4): ").strip() or "4"
    type_map = {"1": "samples", "2": "inspection_tasks", "3": "rectification", "4": "all"}

    supplier_name = input("按供应商名称筛选 (可留空): ").strip() or None
    start_date = input("开始日期 (可留空): ").strip() or None
    end_date = input("结束日期 (可留空): ").strip() or None

    print("正在导出...")
    res = export_to_excel(
        query_type=type_map.get(choice, "all"),
        supplier_name=supplier_name,
        start_date=start_date,
        end_date=end_date,
        operator="admin"
    )

    if res.get("success"):
        print("\n✅ 导出成功！")
        print(f"   文件路径: {res['filepath']}")
        rc = res.get("record_count")
        if isinstance(rc, dict):
            print(f"   样品记录: {rc.get('samples', 0)} 条")
            print(f"   检测任务: {rc.get('tasks', 0)} 条")
            print(f"   整改工单: {rc.get('rectifications', 0)} 条")
        else:
            print(f"   共导出: {rc} 条记录")
    else:
        print(f"\n❌ 导出失败: {res.get('error')}")


def run_demo():
    print("\n" + "=" * 60)
    print("   系统演示：2个样品从登记到出报告的完整流程")
    print("=" * 60)

    print("\n【第1步】登记2个样品...")
    s1 = register_sample(
        supplier_code="SUP001", sample_type="电子元器件", product_name="集成电路芯片",
        supplier_batch="BAT20260601A", product_spec="规格-A", quantity=100,
        received_by="质检部-李明", operator="demo"
    )
    s2 = register_sample(
        supplier_code="SUP003", sample_type="塑料材料", product_name="ABS工程塑料颗粒",
        supplier_batch="BAT20260602B", product_spec="规格-B", quantity=500,
        received_by="质检部-李明", operator="demo"
    )
    for s in [s1, s2]:
        if s.get("success"):
            print(f"  ✅ 样品 {s['sample_code']} | {s['supplier_name']} | {s['status']}")
            print(f"     二维码文件: {s['qr_code_path']}")
        else:
            print(f"  ❌ 失败: {s.get('error')}")

    print("\n【第2步】自动分配检测任务...")
    auto_assign_pending_samples(operator="demo")
    for sc in [s1.get("sample_code"), s2.get("sample_code")]:
        if not sc:
            continue
        detail = get_sample_inspection_results(sc)
        if detail:
            print(f"  📦 样品 {sc} - {detail['product_name']}")
            for t in detail["tasks"]:
                print(f"     · {t['task_code']} | {t['inspection_item']:8s} | "
                      f"实验室:{t['laboratory']:6s} | 检测员:{t['inspector']:6s} | 标准:{t['standard']}")

    print("\n【第3步】录入检测结果（样品1全部合格，样品2部分不合格）...")
    r1 = get_sample_inspection_results(s1["sample_code"])
    r2 = get_sample_inspection_results(s2["sample_code"])

    print(f"\n  📦 样品 {s1['sample_code']} - 集成电路芯片 (全部合格)")
    for t in r1["tasks"]:
        if t["inspection_item"] == "电气性能":
            val = 98.5
        elif t["inspection_item"] == "尺寸测量":
            val = 5.0
        elif t["inspection_item"] == "可靠性测试":
            val = 2500
        else:
            val = 0.0
        res = submit_inspection_result(t["task_code"], val, t["inspector"], operator="demo")
        icon = "✅" if res.get("result_status") == "合格" else "❌"
        print(f"     {icon} {t['inspection_item']}: {val}{t.get('unit','')} -> {res.get('result_status')}")

    print(f"\n  📦 样品 {s2['sample_code']} - ABS工程塑料颗粒 (故意让冲击强度不合格)")
    for t in r2["tasks"]:
        if t["inspection_item"] == "冲击强度":
            val = 30.0
        elif t["inspection_item"] == "熔融指数":
            val = 10.0
        elif t["inspection_item"] == "密度":
            val = 1.05
        elif t["inspection_item"] == "热变形温度":
            val = 125
        else:
            val = 0.0
        res = submit_inspection_result(t["task_code"], val, t["inspector"], operator="demo")
        icon = "✅" if res.get("result_status") == "合格" else "❌"
        print(f"     {icon} {t['inspection_item']}: {val}{t.get('unit','')} -> {res.get('result_status')}")

    print("\n【第4步】为不合格样品自动创建整改工单...")
    rect_res = auto_create_rectification_for_failed(operator="demo")
    print(f"  不合格样品数: {rect_res.get('total_failed')}")
    print(f"  新建整改工单: {rect_res.get('new_created')}")
    for d in rect_res.get("details", []):
        if d.get("success"):
            print(f"  ✅ 工单 {d['rectify_code']} | 截止: {d['deadline']}")
            if d.get("supplier_level_alert") and d["supplier_level_alert"].get("new_level"):
                la = d["supplier_level_alert"]
                print(f"     ⚠️ 供应商降级: {la.get('old_level')} → {la.get('new_level')} ({la.get('level_description')})")

    print("\n【第5步】检查超期整改工单升级机制...")
    esc = check_and_escalate_overdue(operator="demo")
    print(f"  检查数量: {esc.get('checked')}，升级数量: {esc.get('escalated')} (新工单不会升级，需等48小时)")

    print("\n【第6步】生成月度质量报告...")
    now = datetime.now()
    rpt = generate_monthly_report(year=now.year, month=now.month, operator="demo")
    if rpt.get("success"):
        print(f"  ✅ {rpt['period']} 月度报告已生成")
        print(f"     📄 PDF: {rpt['pdf_path']}")
        print(f"     📊 Excel: {rpt['excel_path']}")
        print(f"     📈 样品总数: {rpt['stats']['total_samples']}")
        print(f"     📈 整体合格率: {rpt['stats']['pass_rate']}%")
        print(f"     📈 平均检测周期: {rpt['stats']['avg_cycle_days']} 天")
        print(f"     📈 参与供应商: {rpt['stats']['supplier_count']} 家")

    print("\n【第7步】导出全部数据到Excel...")
    exp = export_to_excel(query_type="all", operator="demo")
    if exp.get("success"):
        print(f"  ✅ 导出成功: {exp['filepath']}")
        rc = exp["record_count"]
        print(f"     样品: {rc['samples']} 条, 任务: {rc['tasks']} 条, 工单: {rc['rectifications']} 条")

    print("\n" + "=" * 60)
    print("   ✅ 演示完成！所有业务流程已跑通。")
    print("   你可以在 reports/ 下找到报告文件，exports/ 下找到导出文件")
    print("=" * 60 + "\n")


def main():
    init_system()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "demo":
            run_demo()
        elif cmd == "report":
            y = int(sys.argv[2]) if len(sys.argv) > 2 else None
            m = int(sys.argv[3]) if len(sys.argv) > 3 else None
            now = datetime.now()
            rpt = generate_monthly_report(year=y or now.year, month=m or now.month)
            print(json.dumps(rpt, ensure_ascii=False, indent=2))
        elif cmd == "export":
            et = sys.argv[2] if len(sys.argv) > 2 else "all"
            res = export_to_excel(query_type=et)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"未知命令: {cmd}")
            print("可用命令: demo / report [年] [月] / export [samples|tasks|rectification|all]")
        return

    while True:
        print_menu()
        choice = input("请选择操作: ").strip()
        if choice == "0":
            print("再见！")
            break
        elif choice == "1":
            action_register_sample()
        elif choice == "2":
            action_submit_result()
        elif choice == "3":
            action_query()
        elif choice == "4":
            action_generate_report()
        elif choice == "5":
            action_export()
        elif choice == "6":
            run_demo()
        else:
            print("无效选择，请重试")


if __name__ == "__main__":
    main()
