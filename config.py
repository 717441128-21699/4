import os
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'quality_management.db')}"

REPORT_DIR = os.path.join(BASE_DIR, 'reports')
EXPORT_DIR = os.path.join(BASE_DIR, 'exports')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
QR_DIR = os.path.join(BASE_DIR, 'qrcodes')

for d in [REPORT_DIR, EXPORT_DIR, LOG_DIR, QR_DIR]:
    os.makedirs(d, exist_ok=True)

SAMPLE_CODE_PREFIX = "SMP"
TASK_CODE_PREFIX = "TSK"
RECTIFY_CODE_PREFIX = "RCT"

INSPECTION_STANDARDS = {
    "电子元器件": {
        "外观检查": {"min": 0, "max": 1, "unit": "级", "method": "目视检查"},
        "尺寸测量": {"tolerance": 0.05, "unit": "mm", "method": "千分尺测量"},
        "电气性能": {"min": 95, "max": 100, "unit": "%", "method": "综合测试仪"},
        "可靠性测试": {"min": 1000, "unit": "小时", "method": "高温老化"}
    },
    "金属材料": {
        "化学成分": {"tolerance": 0.02, "unit": "%", "method": "光谱分析仪"},
        "硬度测试": {"min": 180, "max": 220, "unit": "HV", "method": "维氏硬度计"},
        "拉伸强度": {"min": 450, "unit": "MPa", "method": "万能试验机"},
        "金相分析": {"min": 0, "max": 1, "unit": "级", "method": "金相显微镜"}
    },
    "塑料材料": {
        "熔融指数": {"min": 8, "max": 12, "unit": "g/10min", "method": "熔融指数仪"},
        "密度": {"tolerance": 0.02, "unit": "g/cm³", "method": "密度天平"},
        "冲击强度": {"min": 50, "unit": "kJ/m²", "method": "冲击试验机"},
        "热变形温度": {"min": 120, "unit": "℃", "method": "热变形试验机"}
    },
    "包装材料": {
        "厚度": {"tolerance": 0.01, "unit": "mm", "method": "测厚仪"},
        "拉伸强度": {"min": 20, "unit": "MPa", "method": "拉力试验机"},
        "密封性": {"min": 0, "max": 1, "unit": "级", "method": "压力衰减法"},
        "印刷质量": {"min": 0, "max": 1, "unit": "级", "method": "目视比对"}
    }
}

LABORATORIES = {
    "电子实验室": ["电子元器件"],
    "材料实验室": ["金属材料", "塑料材料"],
    "包装实验室": ["包装材料"],
    "综合实验室": ["电子元器件", "金属材料", "塑料材料", "包装材料"]
}

INSPECTORS = {
    "电子实验室": ["张伟", "李娜", "王强"],
    "材料实验室": ["刘洋", "陈静", "赵磊"],
    "包装实验室": ["孙丽", "周明", "吴芳"],
    "综合实验室": ["郑华", "黄丽", "林峰"]
}

SUPPLIER_LEVELS = {
    "A": {"priority": 1, "description": "战略合作伙伴", "sample_ratio": 0.05},
    "B": {"priority": 2, "description": "合格供应商", "sample_ratio": 0.1},
    "C": {"priority": 3, "description": "限制供应商", "sample_ratio": 0.2},
    "D": {"priority": 4, "description": "不合格供应商", "sample_ratio": 1.0}
}

RECTIFY_DEADLINE_HOURS = 48
CONSECUTIVE_FAIL_THRESHOLD = 3
ESCALATION_ROLE = "供应商主管"

WECOM_WEBHOOK_URL = ""
DINGTALK_WEBHOOK_URL = ""

LOG_FILE = os.path.join(LOG_DIR, "system.log")
AUDIT_LOG_FILE = os.path.join(LOG_DIR, "audit.log")
