import os
import json
import re
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

import qrcode

from config import (
    SAMPLE_CODE_PREFIX, QR_DIR, INSPECTION_STANDARDS
)
from models import SessionLocal, Sample, Supplier
from notification import log_operation, push_alert, write_audit_log


def generate_sample_code() -> str:
    now_str = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    short_uuid = uuid.uuid4().hex[:4].upper()
    return f"{SAMPLE_CODE_PREFIX}{now_str}{short_uuid}"


def generate_qr_code(sample_code: str, sample_info: Dict[str, Any]) -> str:
    qr_data = {
        "sample_code": sample_code,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **{k: v for k, v in sample_info.items() if v is not None}
    }
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(json.dumps(qr_data, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    qr_path = os.path.join(QR_DIR, f"{sample_code}.png")
    img.save(qr_path)
    return qr_path


def parse_qr_data(qr_text: str) -> Dict[str, Any]:
    try:
        data = json.loads(qr_text)
        return data
    except (json.JSONDecodeError, TypeError):
        pass
    patterns = {
        "sample_code": rf"({SAMPLE_CODE_PREFIX}\d{{12}})",
        "supplier_code": r"(SUP\d{3})",
        "supplier_batch": r"BAT[:：](\w+)",
        "sample_type": r"TYP[:：]([\u4e00-\u9fa5\w]+)",
        "product_name": r"PRD[:：]([\u4e00-\u9fa5\w\s\-]+)",
    }
    result = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, qr_text)
        if m:
            result[key] = m.group(1)
    return result


def validate_sample_type(sample_type: str) -> bool:
    return sample_type in INSPECTION_STANDARDS


def register_sample(
    supplier_code: str,
    sample_type: str,
    product_name: str,
    supplier_batch: Optional[str] = None,
    product_spec: Optional[str] = None,
    quantity: int = 1,
    received_by: str = "system",
    remarks: Optional[str] = None,
    operator: str = "system"
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        supplier = db.query(Supplier).filter(
            Supplier.supplier_code == supplier_code
        ).first()
        if not supplier:
            return {"success": False, "error": f"供应商代码 {supplier_code} 不存在"}

        if not validate_sample_type(sample_type):
            return {
                "success": False,
                "error": f"样品类型 {sample_type} 不支持，可选类型: {list(INSPECTION_STANDARDS.keys())}"
            }

        sample_code = generate_sample_code()

        sample = Sample(
            sample_code=sample_code,
            supplier_id=supplier.id,
            supplier_batch=supplier_batch,
            sample_type=sample_type,
            product_name=product_name,
            product_spec=product_spec,
            quantity=quantity,
            received_by=received_by,
            status="待检测",
            remarks=remarks
        )
        db.add(sample)
        db.flush()

        sample_info = {
            "supplier_code": supplier_code,
            "supplier_name": supplier.supplier_name,
            "sample_type": sample_type,
            "product_name": product_name,
            "supplier_batch": supplier_batch,
        }
        qr_path = generate_qr_code(sample_code, sample_info)
        sample.qr_code_path = qr_path

        db.commit()
        db.refresh(sample)

        log_operation(
            "样品登记",
            f"登记样品 {sample_code}，供应商:{supplier.supplier_name}，"
            f"类型:{sample_type}，产品:{product_name}",
            operator=operator,
            target_type="Sample",
            target_id=sample.id
        )
        write_audit_log(
            f"样品登记 | 编号:{sample_code} | 供应商:{supplier.supplier_name} | "
            f"类型:{sample_type} | 产品:{product_name}"
        )
        push_alert(
            "新样品登记通知",
            f"**样品编号**: {sample_code}\n"
            f"**供应商**: {supplier.supplier_name} ({supplier_code})\n"
            f"**样品类型**: {sample_type}\n"
            f"**产品名称**: {product_name}\n"
            f"**数量**: {quantity}\n"
            f"**接收人**: {received_by}",
            level="info"
        )

        return {
            "success": True,
            "sample_code": sample_code,
            "sample_id": sample.id,
            "qr_code_path": qr_path,
            "supplier_name": supplier.supplier_name,
            "status": sample.status
        }
    except Exception as e:
        db.rollback()
        log_operation(
            "样品登记-失败",
            f"登记失败: {str(e)}",
            operator=operator
        )
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def scan_and_register(
    qr_text: str,
    operator: str = "system"
) -> Dict[str, Any]:
    data = parse_qr_data(qr_text)
    if not data:
        return {"success": False, "error": "无法解析二维码内容"}

    if "sample_code" in data:
        db = SessionLocal()
        try:
            existing = db.query(Sample).filter(
                Sample.sample_code == data["sample_code"]
            ).first()
            if existing:
                return {
                    "success": True,
                    "message": "样品已存在",
                    "sample_code": existing.sample_code,
                    "status": existing.status
                }
        finally:
            db.close()

    required = ["supplier_code", "sample_type", "product_name"]
    missing = [k for k in required if k not in data]
    if missing:
        return {"success": False, "error": f"二维码缺少必要信息: {missing}"}

    return register_sample(
        supplier_code=data["supplier_code"],
        sample_type=data["sample_type"],
        product_name=data["product_name"],
        supplier_batch=data.get("supplier_batch"),
        product_spec=data.get("product_spec"),
        quantity=data.get("quantity", 1),
        received_by=data.get("received_by", operator),
        remarks=data.get("remarks"),
        operator=operator
    )


def get_sample_by_code(sample_code: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        sample = db.query(Sample).filter(
            Sample.sample_code == sample_code
        ).first()
        if not sample:
            return None
        return {
            "sample_id": sample.id,
            "sample_code": sample.sample_code,
            "supplier_code": sample.supplier.supplier_code if sample.supplier else None,
            "supplier_name": sample.supplier.supplier_name if sample.supplier else None,
            "sample_type": sample.sample_type,
            "product_name": sample.product_name,
            "product_spec": sample.product_spec,
            "supplier_batch": sample.supplier_batch,
            "quantity": sample.quantity,
            "status": sample.status,
            "received_at": sample.received_at.strftime("%Y-%m-%d %H:%M:%S") if sample.received_at else None,
            "qr_code_path": sample.qr_code_path,
            "remarks": sample.remarks
        }
    finally:
        db.close()
