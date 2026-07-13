from app.core.bootstrap import create_schema
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import PsychologicalReport
from app.services.tools import ToolOrchestrationService

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    raise RuntimeError("请先安装 requirements.txt 中的 mcp 依赖") from exc


mcp = FastMCP("mindbridge-python-tools")


@mcp.tool()
def mindbridge_excel_report(report_id: int) -> str:
    """将心理风险报告写入 Excel 台账。"""
    create_schema()
    db = SessionLocal()
    try:
        report = db.get(PsychologicalReport, report_id)
        if report is None:
            return f"report {report_id} not found"
        record = ToolOrchestrationService(db, get_settings()).write_excel(report)
        return f"success: {record.file_path}"
    finally:
        db.close()


@mcp.tool()
def mindbridge_case_create(report_id: int) -> str:
    """为心理报告创建或返回风险个案。"""
    create_schema()
    db = SessionLocal()
    try:
        report = db.get(PsychologicalReport, report_id)
        if report is None:
            return f"report {report_id} not found"
        case = ToolOrchestrationService(db, get_settings()).create_case(report)
        return f"success: caseId={case.id}, reportId={case.report_id}, status={case.status}"
    finally:
        db.close()


@mcp.tool()
def mindbridge_alert_send(case_id: int) -> str:
    """为风险个案发送或记录辅导员预警。"""
    create_schema()
    db = SessionLocal()
    try:
        from app.models.entities import RiskCase

        case = db.get(RiskCase, case_id)
        if case is None:
            return f"case {case_id} not found"
        record = ToolOrchestrationService(db, get_settings()).send_case_alert(case)
        return f"{record.status}: caseId={case_id}, {record.channel} -> {record.recipient}: {record.message}"
    finally:
        db.close()


@mcp.tool()
def mindbridge_alert_ack(case_id: int, actor: str, note: str = "") -> str:
    """标记风险个案已被辅导员确认。"""
    create_schema()
    db = SessionLocal()
    try:
        case = ToolOrchestrationService(db, get_settings()).acknowledge_case(case_id, actor, note)
        return f"success: caseId={case.id}, status={case.status}, acknowledgedBy={case.acknowledged_by}"
    except RuntimeError as exc:
        return str(exc)
    finally:
        db.close()


@mcp.tool()
def mindbridge_case_note_add(case_id: int, actor: str, note: str) -> str:
    """为风险个案追加跟进备注。"""
    create_schema()
    db = SessionLocal()
    try:
        record = ToolOrchestrationService(db, get_settings()).add_case_note(case_id, actor, note)
        return f"success: noteId={record.id}, caseId={record.case_id}"
    except RuntimeError as exc:
        return str(exc)
    finally:
        db.close()


@mcp.tool()
def mindbridge_alert_notify(report_id: int) -> str:
    """发送高风险预警邮件并记录结果。"""
    create_schema()
    db = SessionLocal()
    try:
        report = db.get(PsychologicalReport, report_id)
        if report is None:
            return f"report {report_id} not found"
        record = ToolOrchestrationService(db, get_settings()).notify(report)
        return f"{record.status}: {record.channel} -> {record.recipient}: {record.message}"
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
