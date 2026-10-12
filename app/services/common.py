from datetime import date
from ..extensions import db
from ..models import Setting, ScoreCategory, EvaluationPeriod, PerformanceScore, Notification, User, ADMIN

DEFAULT_SETTINGS = {
    "ew_drop_points": "10",        # EW-02
    "ew_low_score": "60",          # EW-03
    "ew_kpi_low_pct": "50",        # EW-04
    "ew_reject_count": "2",        # EW-06
    "default_cap": "100",
}


class BusinessError(Exception):
    def __init__(self, code, message, status=409, fields=None):
        super().__init__(message)
        self.code, self.message, self.status, self.fields = code, message, status, fields or {}


def get_setting(key):
    s = db.session.get(Setting, key)
    return s.value if s else DEFAULT_SETTINGS.get(key)


def set_setting(key, value):
    s = db.session.get(Setting, key)
    if s:
        s.value = str(value)
    else:
        db.session.add(Setting(key=key, value=str(value)))


def all_settings():
    d = dict(DEFAULT_SETTINGS)
    for s in Setting.query.all():
        d[s.key] = s.value
    return d


def category_tuples():
    return [(c.name, c.min_score, c.max_score, c.color) for c in ScoreCategory.query.order_by(ScoreCategory.min_score).all()]


def category_color(name):
    c = ScoreCategory.query.filter_by(name=name).first()
    return c.color if c else "#5F6B7A"


def notify(user_id, type_, title, body=None, link=None):
    if user_id:
        db.session.add(Notification(user_id=user_id, type=type_, title=title, body=body, link=link))


def notify_admins(type_, title, body=None, link=None):
    for u in User.query.filter_by(role=ADMIN, status="ACTIVE").all():
        notify(u.id, type_, title, body, link)


def ordered_periods():
    return EvaluationPeriod.query.order_by(EvaluationPeriod.start_date).all()


def latest_scored_period():
    """Periode terbaru yang memiliki skor resmi (CALCULATED/FINALIZED); bila belum ada, skor sementara apa pun."""
    base = (EvaluationPeriod.query.join(PerformanceScore, PerformanceScore.period_id == EvaluationPeriod.id))
    return (base.filter(PerformanceScore.status.in_(["CALCULATED", "FINALIZED"])).order_by(EvaluationPeriod.start_date.desc()).first()
            or base.order_by(EvaluationPeriod.start_date.desc()).first())


def pick_period(period_id=None):
    if period_id:
        return db.session.get(EvaluationPeriod, int(period_id))
    return latest_scored_period() or EvaluationPeriod.query.order_by(EvaluationPeriod.start_date.desc()).first()


def today():
    return date.today()
