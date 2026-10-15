# Create your models here.
from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    """
    Extends Django's built-in User with a role, rather than replacing
    User with a custom model. Swapping AUTH_USER_MODEL on a project
    that already has migrations applied (and a populated database) is
    disruptive — this OneToOne approach adds a role without touching
    the existing auth tables.
    """

    ROLE_VIEWER = "viewer"
    ROLE_ACCOUNTANT = "accountant"
    ROLE_ADMIN = "admin"

    ROLE_CHOICES = [
        (ROLE_VIEWER, "Viewer"),
        (ROLE_ACCOUNTANT, "Accountant"),
        (ROLE_ADMIN, "Admin"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_VIEWER,
    )

    def __str__(self):
        return f"{self.user.username} ({self.role})"

    @property
    def can_reconcile(self):
        return self.role in (self.ROLE_ACCOUNTANT, self.ROLE_ADMIN)

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    # Every user gets a profile automatically — nothing else in the
    # codebase has to remember to create one.
    if created:
        UserProfile.objects.get_or_create(user=instance)


class CauseFeedback(models.Model):
    """
    One accountant's confirm/reject decision on one suggested cause for
    one pattern — the ONLY thing the learned scorer
    (analytics/explanation/learning/) trains on.

    WHY THIS IS THE ONLY PERSISTED TABLE IN THE WHOLE ANALYTICS LAYER
    ─────────────────────────────────────────────────────────────────
    Every other part of Phase 1/2/3 is stateless by design (see
    ARCHITECTURE.md §1/§15/§19, and views.py's analyze_exceptions /
    explain_pattern docstrings) — reconciliation results are never
    saved, patterns are recomputed fresh on every request. Feedback is
    the one thing that genuinely MUST outlive a single request: it's
    training data that accumulates over many separate reconciliations,
    and a learned model is only as good as how much of it exists.

    `features` stores exactly what
    learning.feature_vector.build_feature_vector produced at the
    moment the feedback was given (see learning/schema.py's
    FeedbackRecord docstring for why capturing it then, rather than
    recomputing it later, matters). Storing it as JSON here — rather
    than normalizing it into columns — mirrors how genericBank.json /
    genericTally.json store their own structured-but-variable config:
    the feature vocabulary differs per cause_id and changes whenever
    catalogue.py's conditions change, so a fixed column schema would
    need a migration every time a condition is added or edited.
    """

    cause_id = models.CharField(max_length=100, db_index=True)
    pattern_type = models.CharField(max_length=100)
    exception_type = models.CharField(max_length=100)
    features = models.JSONField()
    confirmed = models.BooleanField()

    # The RAW (continuous) PatternContext.aggregate_stats at the moment
    # feedback was given — e.g. {"average_absolute_amount_difference":
    # 251.0, "reference_match_rate": 0.94, ...}. Deliberately separate
    # from `features` above:
    #   - `features` holds BOOLEAN evaluations of an EXISTING cause's
    #     already-declared conditions — everything (a)/(b)/(c) need to
    #     score a cause that's already in catalogue.py.
    #   - `raw_stats` holds the CONTINUOUS numbers themselves — needed
    #     to discover a cause that ISN'T in catalogue.py yet (point
    #     (d), see analytics/explanation/discovery/README.md), since a
    #     boolean "was condition X true" tells you nothing about
    #     conditions nobody thought to write down yet.
    # default=dict so existing rows created before this field existed
    # (or via any code path that doesn't set it) don't need a data
    # migration — they simply have nothing for discovery to mine,
    # which is a fine, valid state, not an error.
    raw_stats = models.JSONField(default=dict, blank=True)

    # Free-text, optional — a human-supplied pointer back to which
    # reconciliation this came from (e.g. a filename or a date range
    # the accountant typed in). Never a hard foreign key, because
    # reconciliation runs themselves are never persisted anywhere to
    # link to.
    reconciliation_reference = models.CharField(max_length=255, blank=True, null=True)

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cause_feedback",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["cause_id", "created_at"])]

    def __str__(self):
        verdict = "confirmed" if self.confirmed else "rejected"
        return f"{self.cause_id} {verdict} by {self.submitted_by_id or 'unknown'} at {self.created_at}"

    def to_feedback_record(self):
        """Converts one DB row into the plain-dataclass
        `learning.schema.FeedbackRecord` the trainer actually consumes
        — keeps the trainer/calibration code (which is deliberately
        Django-free, like the rest of Phase 1/2/3) from importing
        Django models directly."""
        from .analytics.explanation.learning.schema import FeedbackRecord

        return FeedbackRecord(
            cause_id=self.cause_id,
            pattern_type=self.pattern_type,
            exception_type=self.exception_type,
            features=self.features,
            confirmed=self.confirmed,
            recorded_at=self.created_at.isoformat(),
            source="accountant",
            reconciliation_reference=self.reconciliation_reference,
        )

    def to_resolved_case(self):
        """Converts one DB row into the plain-dataclass
        `discovery.schema.ResolvedCase` — the raw-stats counterpart to
        `to_feedback_record()` above, consumed by point (d)'s
        association-rule mining (analytics/explanation/discovery/)
        instead of by the learned scorer. Keeps discovery/ Django-free
        the same way learning/ is, for the same reason."""
        from .analytics.explanation.discovery.schema import ResolvedCase

        return ResolvedCase(
            cause_id=self.cause_id,
            pattern_type=self.pattern_type,
            exception_type=self.exception_type,
            raw_stats=self.raw_stats or {},
            confirmed=self.confirmed,
            recorded_at=self.created_at.isoformat(),
        )


class ReconciliationRun(models.Model):
    """
    A durable copy of one reconciliation result — the ONE deliberate
    exception, alongside CauseFeedback, to this codebase's otherwise
    strict statelessness (see ARCHITECTURE.md / every analytics view's
    own docstring on why nothing else is persisted).

    WHY THIS EXISTS
    ──────────────────
    Before this model, `upload_files`/`upload_files_multi` computed a
    full reconciliation result and handed it straight back to the
    browser — living ONLY in the frontend's React state. A page
    refresh, a crashed tab, or the server restarting mid-session lost
    it completely; there was nothing to recover. This table exists
    specifically so a finished reconciliation survives all of that —
    the moment `reconcile()` succeeds, the result is written here
    before it's ever returned to the client, so "the request already
    got a 200 back" and "the data is safely stored" happen together,
    not as two separate steps that could fail independently.

    WHY THIS IS A DATABASE TABLE, NOT DJANGO'S CACHE FRAMEWORK
    ────────────────────────────────────────────────────────────────
    Django's default cache backend (LocMemCache) lives in process
    memory — a server restart, deploy, or crash wipes it exactly like
    the frontend React state it would otherwise be replacing. That is
    the OPPOSITE of "survives a power cut." A real, durable outcome
    needs a real row in a real database — which is what this is, and
    why it only became practical to add cleanly once the project
    could point at Neon instead of a share-nothing local process. (If
    a later need is "make repeated identical requests cheap", THAT is
    what Django's cache framework is for — see
    analytics/explanation/cache.py's own use of it for exactly that,
    a different problem from durability.)

    WHAT'S STORED
    ─────────────────
    `result` is the exact JSON dictionary `ReconciliationService.reconcile()`
    (or `MultiSourceReconciliationService.reconcile()`) already
    produced — the same shape already returned to the browser, stored
    verbatim rather than re-derived into a different schema. This
    means restoring a run is just "hand this dict back to the
    frontend", not a second code path that could drift from the real
    response shape over time.
    """

    RUN_TYPE_SINGLE = "single"
    RUN_TYPE_MULTI = "multi"
    RUN_TYPE_CHOICES = [
        (RUN_TYPE_SINGLE, "Bank vs Tally"),
        (RUN_TYPE_MULTI, "Multi-source"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reconciliation_runs",
    )
    run_type = models.CharField(max_length=10, choices=RUN_TYPE_CHOICES)
    result = models.JSONField()

    # A tiny, quick-to-query duplicate of a few summary numbers so a
    # history list view never needs to deserialize the (potentially
    # large) full `result` blob just to show "87% matched, 214
    # exceptions" in a list row.
    summary_snapshot = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.run_type} run by {self.user_id} at {self.created_at}"
