import logging
import json
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

import requests

from config import LOG_FILE, AUDIT_LOG_FILE, WECOM_WEBHOOK_URL, DINGTALK_WEBHOOK_URL
from models import SessionLocal, OperationLog, NotificationRecord


def _setup_file_logger():
    logger = logging.getLogger("quality_system")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


file_logger = _setup_file_logger()


def log_operation(operation_type, content, operator="system",
                  target_type=None, target_id=None, ip_address=None):
    db = SessionLocal()
    try:
        log = OperationLog(
            operation_type=operation_type,
            operator=operator,
            target_type=target_type,
            target_id=target_id,
            content=content,
            ip_address=ip_address
        )
        db.add(log)
        db.commit()
        file_logger.info(
            f"[操作日志] {operation_type} | 操作人:{operator} | "
            f"目标:{target_type}/{target_id} | 内容:{content}"
        )
    except Exception as e:
        file_logger.error(f"记录操作日志失败: {e}")
        db.rollback()
    finally:
        db.close()


def write_audit_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        file_logger.error(f"写入审计日志失败: {e}")


def send_wecom_notification(title, content, mentioned_mobile_list=None):
    db = SessionLocal()
    record = NotificationRecord(
        notification_type="wecom",
        title=title,
        content=content,
        channel="wecom"
    )
    try:
        db.add(record)
        db.flush()
        if not WECOM_WEBHOOK_URL:
            file_logger.warning(
                f"[企业微信推送] 未配置Webhook，仅记录本地 | 标题:{title}"
            )
            record.is_sent = False
            record.error_msg = "未配置Webhook URL"
            db.commit()
            return False

        md_content = f"## **{title}**\n\n{content}\n\n"
        md_content += f"> 推送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        payload = {
            "msgtype": "markdown",
            "markdown": {"content": md_content}
        }
        if mentioned_mobile_list:
            payload["markdown"]["mentioned_mobile_list"] = mentioned_mobile_list

        resp = requests.post(WECOM_WEBHOOK_URL, json=payload, timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            record.is_sent = True
            record.sent_at = datetime.now()
            file_logger.info(f"[企业微信推送] 发送成功 | 标题:{title}")
            db.commit()
            return True
        else:
            record.error_msg = result.get("errmsg", "未知错误")
            file_logger.error(
                f"[企业微信推送] 发送失败 | 标题:{title} | 错误:{record.error_msg}"
            )
            db.commit()
            return False
    except Exception as e:
        db.rollback()
        record.error_msg = str(e)
        file_logger.error(f"[企业微信推送] 异常 | 标题:{title} | 异常:{e}")
        return False
    finally:
        db.close()


def send_dingtalk_notification(title, content, at_mobiles=None):
    db = SessionLocal()
    record = NotificationRecord(
        notification_type="dingtalk",
        title=title,
        content=content,
        channel="dingtalk"
    )
    try:
        db.add(record)
        db.flush()
        if not DINGTALK_WEBHOOK_URL:
            file_logger.warning(
                f"[钉钉推送] 未配置Webhook，仅记录本地 | 标题:{title}"
            )
            record.is_sent = False
            record.error_msg = "未配置Webhook URL"
            db.commit()
            return False

        md_content = f"## {title}\n\n{content}\n\n"
        md_content += f"**推送时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": md_content}
        }
        if at_mobiles:
            payload["at"] = {"atMobiles": at_mobiles, "isAtAll": False}

        resp = requests.post(DINGTALK_WEBHOOK_URL, json=payload, timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            record.is_sent = True
            record.sent_at = datetime.now()
            file_logger.info(f"[钉钉推送] 发送成功 | 标题:{title}")
            db.commit()
            return True
        else:
            record.error_msg = result.get("errmsg", "未知错误")
            file_logger.error(
                f"[钉钉推送] 发送失败 | 标题:{title} | 错误:{record.error_msg}"
            )
            db.commit()
            return False
    except Exception as e:
        db.rollback()
        record.error_msg = str(e)
        file_logger.error(f"[钉钉推送] 异常 | 标题:{title} | 异常:{e}")
        return False
    finally:
        db.close()


def push_alert(title, content, level="info", mentioned_mobiles=None):
    level_map = {
        "info": "ℹ️ 信息",
        "warning": "⚠️ 预警",
        "error": "❌ 异常",
        "urgent": "🚨 紧急"
    }
    level_tag = level_map.get(level, "ℹ️ 信息")
    full_title = f"{level_tag} | {title}"
    send_wecom_notification(full_title, content, mentioned_mobiles)
    send_dingtalk_notification(full_title, content, mentioned_mobiles)
    write_audit_log(f"[预警推送-{level.upper()}] {title} - {content[:100]}")
