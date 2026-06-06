from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from config import DATABASE_URL

Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    supplier_code = Column(String(50), unique=True, index=True, nullable=False)
    supplier_name = Column(String(200), nullable=False)
    contact_person = Column(String(100))
    contact_phone = Column(String(50))
    contact_email = Column(String(100))
    address = Column(String(500))
    level = Column(String(10), default="B")
    consecutive_failures = Column(Integer, default=0)
    total_inspections = Column(Integer, default=0)
    pass_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    samples = relationship("Sample", back_populates="supplier")


class Sample(Base):
    __tablename__ = "samples"

    id = Column(Integer, primary_key=True, index=True)
    sample_code = Column(String(50), unique=True, index=True, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    supplier_batch = Column(String(100))
    sample_type = Column(String(100), nullable=False)
    product_name = Column(String(200), nullable=False)
    product_spec = Column(String(200))
    quantity = Column(Integer, default=1)
    received_by = Column(String(100))
    received_at = Column(DateTime, default=datetime.now)
    qr_code_path = Column(String(500))
    status = Column(String(50), default="待检测")
    remarks = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    supplier = relationship("Supplier", back_populates="samples")
    inspection_tasks = relationship("InspectionTask", back_populates="sample")


class InspectionStandard(Base):
    __tablename__ = "inspection_standards"

    id = Column(Integer, primary_key=True, index=True)
    sample_type = Column(String(100), nullable=False)
    inspection_item = Column(String(100), nullable=False)
    min_value = Column(Float)
    max_value = Column(Float)
    tolerance = Column(Float)
    standard_value = Column(Float)
    unit = Column(String(50))
    method = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)


class InspectionTask(Base):
    __tablename__ = "inspection_tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_code = Column(String(50), unique=True, index=True, nullable=False)
    sample_id = Column(Integer, ForeignKey("samples.id"), nullable=False)
    inspection_item = Column(String(100), nullable=False)
    laboratory = Column(String(100), nullable=False)
    inspector = Column(String(100), nullable=False)
    standard_min = Column(Float)
    standard_max = Column(Float)
    standard_tolerance = Column(Float)
    unit = Column(String(50))
    method = Column(String(200))
    status = Column(String(50), default="待检测")
    result_value = Column(Float)
    result_status = Column(String(50))
    inspected_at = Column(DateTime)
    inspected_by = Column(String(100))
    remarks = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    sample = relationship("Sample", back_populates="inspection_tasks")


class RectificationOrder(Base):
    __tablename__ = "rectification_orders"

    id = Column(Integer, primary_key=True, index=True)
    rectify_code = Column(String(50), unique=True, index=True, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("samples.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("inspection_tasks.id"), nullable=False)
    failed_items = Column(Text, nullable=False)
    description = Column(Text)
    deadline = Column(DateTime, nullable=False)
    is_escalated = Column(Boolean, default=False)
    escalated_at = Column(DateTime)
    status = Column(String(50), default="待整改")
    rectify_measures = Column(Text)
    rectified_at = Column(DateTime)
    verified_by = Column(String(100))
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, index=True)
    operation_type = Column(String(100), nullable=False)
    operator = Column(String(100), default="system")
    target_type = Column(String(100))
    target_id = Column(Integer)
    content = Column(Text)
    ip_address = Column(String(50))
    created_at = Column(DateTime, default=datetime.now, index=True)


class NotificationRecord(Base):
    __tablename__ = "notification_records"

    id = Column(Integer, primary_key=True, index=True)
    notification_type = Column(String(100), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text)
    channel = Column(String(50), default="wecom")
    is_sent = Column(Boolean, default=False)
    sent_at = Column(DateTime)
    error_msg = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_month = Column(String(20), nullable=False)
    report_year = Column(Integer, nullable=False)
    pdf_path = Column(String(500))
    excel_path = Column(String(500))
    generated_by = Column(String(100), default="system")
    generated_at = Column(DateTime, default=datetime.now)
    total_samples = Column(Integer, default=0)
    pass_rate = Column(Float, default=0.0)
    avg_cycle_days = Column(Float, default=0.0)


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _init_standards(db)
        _init_suppliers(db)
        db.commit()
    finally:
        db.close()


def _init_standards(db):
    from config import INSPECTION_STANDARDS
    existing = db.query(InspectionStandard).first()
    if existing:
        return
    for sample_type, items in INSPECTION_STANDARDS.items():
        for item_name, spec in items.items():
            std = InspectionStandard(
                sample_type=sample_type,
                inspection_item=item_name,
                min_value=spec.get("min"),
                max_value=spec.get("max"),
                tolerance=spec.get("tolerance"),
                unit=spec.get("unit"),
                method=spec.get("method")
            )
            db.add(std)


def _init_suppliers(db):
    existing = db.query(Supplier).first()
    if existing:
        return
    demo_suppliers = [
        {"code": "SUP001", "name": "深圳华为电子有限公司", "person": "张三", "phone": "13800138001", "email": "zhangsan@huawei.com", "level": "A"},
        {"code": "SUP002", "name": "上海宝钢钢材贸易有限公司", "person": "李四", "phone": "13800138002", "email": "lisi@baosteel.com", "level": "A"},
        {"code": "SUP003", "name": "广州金发科技股份有限公司", "person": "王五", "phone": "13800138003", "email": "wangwu@kingfa.com", "level": "B"},
        {"code": "SUP004", "name": "东莞合兴包装印刷有限公司", "person": "赵六", "phone": "13800138004", "email": "zhaoliu@hexiang.com", "level": "B"},
        {"code": "SUP005", "name": "苏州国芯电子科技有限公司", "person": "钱七", "phone": "13800138005", "email": "qianqi@guoxin.com", "level": "C"},
    ]
    for s in demo_suppliers:
        sup = Supplier(
            supplier_code=s["code"],
            supplier_name=s["name"],
            contact_person=s["person"],
            contact_phone=s["phone"],
            contact_email=s["email"],
            level=s["level"]
        )
        db.add(sup)
