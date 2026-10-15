from django.shortcuts import render

import logging

from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import CookieJWTAuthentication
from .permissions import IsAccountantOrAdmin
from .services.reconciliationService import ReconciliationService
from .services.multiSourceReconciliationService import MultiSourceReconciliationService, SecondarySourceSpec
from .analytics.service import PatternAnalysisService
from .analytics.explanation.service import PatternExplanationService
from .analytics.reconbot.service import ReconBotService
from .analytics.reconbot.schema import BotContext
from .analytics.explanation.context_builder import PatternContextBuilder
from .analytics.explanation.knowledge.loader import default_catalogue
from .analytics.explanation.learning.feature_vector import build_feature_vector
from .analytics.explanation.learning.learned_scorer import LearnedCauseScoringEngine
from .analytics.explanation.learning import db_bridge
from .models import CauseFeedback, ReconciliationRun

logger = logging.getLogger("parser.views")


def _persist_reconciliation_run(user, run_type, result):
    """Best-effort durability write — see ReconciliationRun's own
    docstring for why this exists and why it's a database row rather
    than Django's cache framework. Deliberately never allowed to break
    the actual HTTP response: a user who successfully reconciled two
    files should still get their result back even if, say, the
    database is briefly unreachable. If this write fails, the failure
    is logged (so it's visible in monitoring) and swallowed here —
    the caller always proceeds to return `result` regardless."""
    try:
        ReconciliationRun.objects.create(
            user=user,
            run_type=run_type,
            result=result,
            summary_snapshot=result.get("summary", {}),
        )
    except Exception as error:
        logger.error("Failed to persist ReconciliationRun (run_type=%s): %s", run_type, error)


def index(request):
    # Kept as a bare fallback / health-check now that Next.js owns the
    # UI — safe to delete entirely once the frontend migration reaches
    # Phase 7 cutover.
    return render(request, "parser/index.html")


@api_view(["POST"])
@authentication_classes([CookieJWTAuthentication])
@permission_classes([IsAccountantOrAdmin])
def upload_files(request):
    """
    POST /api/v1/reconcile/

    Same request/response contract as the original /api/upload/ —
    multipart bank_file + tally_file in, the same JSON shape out (see
    §1.2 of the migration guide). Only the transport changed: this now
    requires an authenticated accountant/admin instead of being open.

    Verified: request.FILES behaves identically wrapped in @api_view
    as it did under the plain Django view — tested with a real
    multipart upload through the full auth-protected endpoint.
    """

    bank_file = request.FILES.get("bank_file")
    tally_file = request.FILES.get("tally_file")

    if not bank_file or not tally_file:
        return Response(
            {"success": False, "error": "Both bank_file and tally_file are required."},
            status=400,
        )

    try:
        result = ReconciliationService().reconcile(bank_file, tally_file)
    except ValueError as error:
        # Expected, user-fixable problems: missing columns, unsupported
        # file type, etc. — surface the message as-is.
        return Response({"success": False, "error": str(error)}, status=400)
    except Exception as error:
        # Anything unexpected still comes back as JSON (never a raw
        # 500 HTML page) so the frontend can show something sane.
        return Response(
            {"success": False, "error": f"Reconciliation failed: {error}"},
            status=500,
        )

    _persist_reconciliation_run(request.user, ReconciliationRun.RUN_TYPE_SINGLE, result)

    return Response(result)


@api_view(["POST"])
@authentication_classes([CookieJWTAuthentication])
@permission_classes([IsAuthenticated])
def analyze_exceptions(request):
    """
    POST /api/v1/exceptions/analyze/

    Phase 1 + Phase 2 of the analytics layer (see
    parser/analytics/ARCHITECTURE.md). This is a pure re-analysis of
    data the caller already has — reconciliation itself stays
    stateless (see ReconciliationService), so rather than inventing a
    persisted "run" to fetch by ID, this endpoint accepts exactly the
    exception-shaped sub-objects already returned by
    /api/v1/reconcile/: "exceptions", "missingInBank",
    "missingInTally", "duplicates", "signReversalHints". The frontend
    already holds this in memory after a reconcile call (see
    ReconciliationProvider), so no extra round trip or storage is
    required to wire up the "Analyze Exceptions" button.

    Read-only and non-mutating, so any authenticated role (not just
    accountant/admin) may call it — a viewer looking at an already
    -computed reconciliation should be able to see its patterns too.
    """

    payload = request.data
    if not isinstance(payload, dict):
        return Response(
            {"success": False, "error": "Expected a JSON object with reconciliation exception data."},
            status=400,
        )

    has_any_section = any(
        payload.get(key) for key in (
            "exceptions", "missingInBank", "missingInTally",
            "duplicates", "signReversalHints",
        )
    )
    if not has_any_section:
        return Response(
            {"success": False, "error": "No exception data provided to analyze."},
            status=400,
        )

    try:
        result = PatternAnalysisService().analyze(payload)
    except Exception as error:
        return Response(
            {"success": False, "error": f"Pattern analysis failed: {error}"},
            status=500,
        )

    return Response({"success": True, **result.to_dict()})


@api_view(["POST"])
@authentication_classes([CookieJWTAuthentication])
@permission_classes([IsAuthenticated])
def explain_pattern(request, pattern_id):
    """
    POST /api/v1/exceptions/patterns/<pattern_id>/explain/

    Phase 3 (see parser/analytics/explanation/ARCHITECTURE_PHASE3_LOCAL.md).
    A local, deterministic interpretation layer sitting on top of
    Phase 1 + Phase 2 — no LLM, no network call, no API key. Retrieves
    candidate causes from a static domain-knowledge catalogue, scores
    them against the pattern's own evidence with a documented rule-based
    formula, and generates a template-based explanation. Never touches
    reconciliation data, never marks anything resolved.

    Same statelessness rationale as /exceptions/analyze/ (see that
    view's docstring): there is no persisted "pattern" to fetch by ID
    server-side, so this endpoint accepts the SAME exception payload
    already sent to /exceptions/analyze/, deterministically re-derives
    the pattern set (Phase 2 discovery is verified deterministic —
    see parser/analytics/tests/test_patterns.py), and explains the one
    pattern whose pattern_id matches the URL. Re-running Phase 1+2 is
    inexpensive (sub-second at tens of thousands of exceptions — see
    ARCHITECTURE.md §17) and avoids inventing a persistence layer or
    bloating the /analyze/ response with full per-transaction detail
    the frontend doesn't otherwise need.
    """

    payload = request.data
    if not isinstance(payload, dict):
        return Response(
            {"success": False, "error": "Expected a JSON object with reconciliation exception data."},
            status=400,
        )

    try:
        analysis_service = PatternAnalysisService()
        features = analysis_service.feature_extractor.extract_all(payload)
        result = analysis_service.analyze_features(features)
    except Exception as error:
        return Response(
            {"success": False, "error": f"Pattern analysis failed: {error}"},
            status=500,
        )

    pattern = next((p for p in result.patterns if p.pattern_id == pattern_id), None)
    if pattern is None:
        return Response(
            {
                "success": False,
                "error": f"No pattern with id {pattern_id!r} was found for the given data. "
                         f"Patterns are re-derived per request — make sure the same exception "
                         f"data used for /exceptions/analyze/ is being sent here.",
            },
            status=404,
        )

    by_id = {ef.exception_id: ef for ef in features}
    members = [by_id[eid] for eid in pattern.exception_ids if eid in by_id]

    try:
        # Tier 1 (case-based) needs the latest feedback, queried fresh
        # per request — see db_bridge.py's docstring for why this
        # isn't cached the way trained_weights.json is. Tier 2
        # (trained models) still loads from the on-disk JSON file via
        # LearnedCauseScoringEngine's own default, unaffected by this.
        scorer = LearnedCauseScoringEngine(case_indexes=db_bridge.load_case_indexes())
        explanation_service = PatternExplanationService(scorer=scorer)
        explanation = explanation_service.explain(pattern, members)
    except Exception as error:
        # Even an unexpected failure in Phase 3 must not take down
        # access to Phase 1/2 results — the pattern itself is still
        # returned, just without an explanation.
        return Response(
            {
                "success": True,
                "pattern": pattern.to_dict(),
                "explanation": None,
                "error": f"Explanation generation failed: {error}",
            },
            status=200,
        )

    return Response({
        "success": True,
        "pattern": pattern.to_dict(),
        "explanation": explanation.to_dict(),
    })


@api_view(["POST"])
@authentication_classes([CookieJWTAuthentication])
@permission_classes([IsAuthenticated])
def reconbot_ask(request):
    """
    POST /api/v1/reconbot/ask/

    Recon Bot — a conversational INTERFACE over Phase 1/2/3, not a new
    analysis engine (see parser/analytics/reconbot/schema.py's module
    docstring). Every answer is phrased from data the reconciliation,
    pattern-analysis, and explanation engines already computed; the bot
    never inspects raw transactions itself and never calls an external
    AI service — intent classification is rule-based, and any "why"
    question routes straight into Phase 3's existing local, deterministic
    explanation pipeline.

    Same statelessness rationale as /exceptions/analyze/ and
    /exceptions/patterns/<id>/explain/: nothing is persisted server-side,
    so the request carries both the exception payload (to re-derive
    Phase 1/2 fresh, deterministically) and the reconciliation `summary`
    object (for direct-count questions like "how many amount mismatches?").
    A "conversation" is just the client round-tripping the small
    `context` object from the previous response — see BotContext.

    Read-only, like every other analytics endpoint: it cannot modify
    transactions, reconciliation results, or mark anything resolved.

    Request body:
    {
        "message": "why do we have so many amount mismatches?",
        "reconciliation": { ...same exception payload as /exceptions/analyze/... },
        "summary": { ...the "summary" object from /reconcile/'s response... },
        "context": {"lastPatternId": "...", "lastExceptionType": "..."}  // optional
    }
    """

    payload = request.data
    if not isinstance(payload, dict):
        return Response(
            {"success": False, "error": "Expected a JSON object with message/reconciliation/summary."},
            status=400,
        )

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return Response({"success": False, "error": "A non-empty 'message' string is required."}, status=400)

    reconciliation_exceptions = payload.get("reconciliation")
    if not isinstance(reconciliation_exceptions, dict):
        return Response(
            {"success": False, "error": "A 'reconciliation' object (the exception payload) is required."},
            status=400,
        )

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    context = BotContext.from_dict(payload.get("context"))

    try:
        # Same wiring as explain_pattern: give Phase 3's cause scorer
        # access to the latest case-based feedback (Tier 1) alongside
        # any trained models (Tier 2) — see
        # analytics/explanation/learning/README.md §7.4. Without this,
        # the bot's "why" answers would only ever see Tier 0/2, never
        # Tier 1, unlike the /explain/ endpoint.
        scorer = LearnedCauseScoringEngine(case_indexes=db_bridge.load_case_indexes())
        explanation_service = PatternExplanationService(scorer=scorer)
        response = ReconBotService(explanation_service=explanation_service).ask(
            message=message, reconciliation_exceptions=reconciliation_exceptions,
            summary=summary, context=context,
        )
    except Exception as error:
        return Response({"success": False, "error": f"Recon Bot failed to answer: {error}"}, status=500)

    return Response({"success": True, **response.to_dict()})


@api_view(["POST"])
@authentication_classes([CookieJWTAuthentication])
@permission_classes([IsAccountantOrAdmin])
def upload_files_multi(request):
    """
    POST /api/v1/reconcile/multi/

    Multi-source reconciliation: one primary file (e.g. Bank) reconciled
    against 2+ secondary files (e.g. Tally, a Payment Gateway export),
    with a cross-source view layered on top — see
    parser/services/multiSourceReconciliationService.py for the full
    design (hub-and-spoke, zero modifications to ReconciliationService).

    Request (multipart/form-data):
        primary_file        (required) — the hub source, e.g. bank statement
        primary_label        (optional, default "Bank")
        secondary_files       (required, 1+) — repeat the field for each file
        secondary_labels      (required, same count/order as secondary_files)
        secondary_configs     (optional, same count/order) — per-source config
                               filename override; omit to use the generic
                               secondary-source config for every source, or
                               pass "genericTally.json" for a source that's
                               specifically Tally-shaped.

    Existing single bank/tally reconciliation at /api/v1/reconcile/ is
    completely untouched by this endpoint — this is strictly additive.
    """

    primary_file = request.FILES.get("primary_file")
    if not primary_file:
        return Response({"success": False, "error": "primary_file is required."}, status=400)
    primary_label = request.POST.get("primary_label") or "Bank"

    secondary_files = request.FILES.getlist("secondary_files")
    secondary_labels = request.POST.getlist("secondary_labels")
    secondary_configs = request.POST.getlist("secondary_configs")

    if not secondary_files:
        return Response(
            {"success": False, "error": "At least one secondary_files entry is required."}, status=400,
        )
    if len(secondary_files) != len(secondary_labels):
        return Response(
            {
                "success": False,
                "error": f"Got {len(secondary_files)} secondary_files but {len(secondary_labels)} "
                         f"secondary_labels — each secondary file needs exactly one matching label, in order.",
            },
            status=400,
        )

    specs = []
    for i, (file, label) in enumerate(zip(secondary_files, secondary_labels)):
        config_file = secondary_configs[i] if i < len(secondary_configs) and secondary_configs[i] else None
        kwargs = {"name": label, "file": file}
        if config_file:
            kwargs["config_file"] = config_file
        specs.append(SecondarySourceSpec(**kwargs))

    try:
        result = MultiSourceReconciliationService(primary_label=primary_label).reconcile(primary_file, specs)
    except ValueError as error:
        return Response({"success": False, "error": str(error)}, status=400)
    except Exception as error:
        return Response({"success": False, "error": f"Multi-source reconciliation failed: {error}"}, status=500)

    _persist_reconciliation_run(request.user, ReconciliationRun.RUN_TYPE_MULTI, result)

    return Response(result)


@api_view(["POST"])
@authentication_classes([CookieJWTAuthentication])
@permission_classes([IsAccountantOrAdmin])
def submit_cause_feedback(request, pattern_id):
    """
    POST /api/v1/exceptions/patterns/<pattern_id>/causes/feedback/

    Lets an accountant confirm or reject one of Phase 3's suggested
    causes for a pattern — the training data for the learned scorer in
    analytics/explanation/learning/ (see that package's README for the
    full a)/b) design: learning scoring.py's weights from outcomes, and
    calibrating the result into a real probability).

    Same statelessness rationale as /exceptions/analyze/ and
    /exceptions/patterns/<id>/explain/ (see those views' docstrings):
    reconciliation runs aren't persisted anywhere, so this endpoint
    re-derives the pattern and its PatternContext fresh from the same
    exception payload the client already sent to /analyze/, then
    stores only the resulting FEATURE VECTOR (not raw transactions) —
    exactly what analytics/explanation/learning/feature_vector.py
    would have computed for that cause, captured at this moment.

    Restricted to accountant/admin (same as /reconcile/) rather than
    IsAuthenticated-only like the read-only analytics endpoints —
    confirming/rejecting a cause is a judgment call that trains a
    model other users' explanations will later depend on, so it's
    treated as a write action, not a read.

    Request body:
    {
        ...same exception payload as /exceptions/analyze/...,
        "causeId": "BANK_SERVICE_CHARGE",
        "confirmed": true | false,
        "reconciliationReference": "optional free-text note"   // optional
    }
    """

    payload = request.data
    cause_id = payload.get("causeId") if isinstance(payload, dict) else None

    if not isinstance(payload, dict):
        return Response({"success": False, "error": "Expected a JSON object."}, status=400)

    if not cause_id or not isinstance(cause_id, str):
        return Response({"success": False, "error": "A 'causeId' string is required."}, status=400)

    confirmed = payload.get("confirmed")
    if not isinstance(confirmed, bool):
        return Response({"success": False, "error": "A boolean 'confirmed' field is required."}, status=400)

    reconciliation_reference = payload.get("reconciliationReference")

    analysis_service = PatternAnalysisService()
    try:
        features = analysis_service.feature_extractor.extract_all(payload)
        result = analysis_service.analyze_features(features)
    except Exception as error:
        return Response({"success": False, "error": f"Pattern analysis failed: {error}"}, status=500)

    pattern = next((p for p in result.patterns if p.pattern_id == pattern_id), None)
    if pattern is None:
        return Response(
            {
                "success": False,
                "error": f"No pattern with id {pattern_id!r} in the current data — patterns are recomputed "
                         f"fresh each request, so send the exact same exception data you used with /analyze/.",
            },
            status=404,
        )

    catalogue = default_catalogue()
    cause_entry = catalogue.get(cause_id)
    if cause_entry is None:
        return Response({"success": False, "error": f"Unknown cause_id {cause_id!r}."}, status=404)

    by_id = {f.exception_id: f for f in features}
    members = [by_id[eid] for eid in pattern.exception_ids if eid in by_id]
    context = PatternContextBuilder().build(pattern, members)

    feature_vector = build_feature_vector(context, cause_entry)

    CauseFeedback.objects.create(
        cause_id=cause_id,
        pattern_type=pattern.pattern_type,
        exception_type=pattern.exception_type,
        features=feature_vector,
        raw_stats=context.aggregate_stats,
        confirmed=confirmed,
        reconciliation_reference=reconciliation_reference,
        submitted_by=request.user if request.user and request.user.is_authenticated else None,
    )

    return Response({"success": True, "causeId": cause_id, "confirmed": confirmed}, status=201)


@api_view(["GET"])
@authentication_classes([CookieJWTAuthentication])
@permission_classes([IsAuthenticated])
def latest_reconciliation_run(request):
    """
    GET /api/v1/reconciliation-runs/latest/

    Returns the current user's most recently persisted reconciliation
    run (see ReconciliationRun's docstring in models.py for why this
    exists — durability across a refresh/crash/restart, not caching).
    Scoped to `run_type` via an optional `?type=single|multi` query
    param, since the frontend needs to know separately "is there a
    two-source run to restore" and "is there a multi-source run to
    restore" — they're shown on different pages and shouldn't clobber
    each other.

    Response:
    {"success": true, "run": {"runType": "single", "result": {...}, "createdAt": "..."}}
    or
    {"success": true, "run": null}   — nothing persisted yet for this user (a normal state, not an error)
    """
    run_type = request.query_params.get("type")

    queryset = ReconciliationRun.objects.filter(user=request.user)
    if run_type in (ReconciliationRun.RUN_TYPE_SINGLE, ReconciliationRun.RUN_TYPE_MULTI):
        queryset = queryset.filter(run_type=run_type)

    latest = queryset.first()  # Meta.ordering = ["-created_at"], so this IS the most recent
    if latest is None:
        return Response({"success": True, "run": None})

    return Response({
        "success": True,
        "run": {
            "runType": latest.run_type,
            "result": latest.result,
            "createdAt": latest.created_at.isoformat(),
        },
    })
