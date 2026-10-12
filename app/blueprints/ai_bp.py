from flask import Blueprint, render_template, request
from flask_login import current_user, login_required
from ..models import Employee, EvaluationPeriod, AiInsight, EarlyWarning, ADMIN, SUPERVISOR
from ..security.rbac import roles_required, scope_employee_ids
from ..services.common import pick_period
from ..ai import llm_client

bp = Blueprint("ai", __name__)


@bp.route("/ai-insights")
@roles_required(ADMIN, SUPERVISOR)
def ai_insights():
    ids = scope_employee_ids(current_user)
    q = Employee.query.filter(Employee.status.in_(["Active", "Probation"]))
    if ids is not None:
        q = q.filter(Employee.id.in_(ids or [-1]))
    iq = AiInsight.query
    if ids is not None:
        iq = iq.filter(AiInsight.employee_id.in_(ids or [-1]))
    return render_template("ai_insights.html", emps=q.order_by(Employee.name).all(),
                           periods=EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).all(),
                           period=pick_period(request.args.get("period_id")),
                           recent=iq.order_by(AiInsight.id.desc()).limit(10).all(),
                           llm=llm_client.is_enabled(), provider=llm_client.provider(), model=llm_client.model_name())


@bp.route("/ai-chat")
@login_required
def ai_chat():
    return render_template("ai_chat.html", llm=llm_client.is_enabled(), role=current_user.role)
