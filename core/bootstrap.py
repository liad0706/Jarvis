"""Shared bootstrap — initializes all Jarvis infrastructure. Used by CLI and future interfaces."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import get_settings
from core.audit import AuditLog
from core.dynamic_loader import load_dynamic_skills
from core.embeddings import EmbeddingEngine
from core.event_bus import EventBus
from core.memory import Memory
from core.memory_manager import MemoryManager
from core.model_router import ModelRouter
from core.conversation_branch import BranchManager
from core.notifications import NotificationManager
from core.automation import AutomationEngine
from core.proactive_engine import ProactiveEngine
from core.environment_awareness import EnvironmentAwareness
from core.smart_routine import SmartRoutineRunner, MORNING_ROUTINE, EVENING_ROUTINE
from core.action_journal import ActionJournal
from core.pattern_learner import PatternLearner
from core.feedback_loop import FeedbackLoop
from core.network_presence import NetworkPresence
from core.calendar_awareness import CalendarAwareness
from core.observability import MetricsCollector
from core.orchestrator import Orchestrator
from core.permissions import PermissionGate
from core.planner import Planner
from core.progress import ProgressReporter
from core.scheduler import Scheduler, Schedule
from core.skill_store import SkillStore
from core.skill_base import SkillRegistry

from skills.creality_print import CrealityPrintSkill
from skills.creality_api_skill import CrealityAPISkill
from skills.model_downloader import ModelDownloaderSkill
from skills.appointment_booker import AppointmentBookerSkill
from skills.spotify_controller import SpotifySkill
from skills.code_writer import CodeWriterSkill
from skills.self_improve import SelfImproveSkill
from skills.system_control import SystemControlSkill
from skills.smart_home import SmartHomeSkill
from skills.apple_tv import AppleTVSkill
from skills.morning_routine import morning_routine
from skills.evening_routine import evening_routine
from skills.memory_skill import MemorySkill
from skills.introspect import IntrospectSkill
from skills.scheduler_skill import SchedulerSkill
from skills.calendar_skill import CalendarSkill
from skills.presence_skill import PresenceSkill
from skills.restart import RestartSkill
from skills.auto_repair import AutoRepairSkill
from skills.web_research import WebResearchSkill
from skills.screen_reader import ScreenReaderSkill
from skills.file_manager import FileManagerSkill
from skills.document_rag import DocumentRAGSkill
from skills.browser_agent import BrowserAgentSkill
from skills.onboarding import OnboardingSkill
from skills.soul_setup import SoulSetupSkill
from core.code_interpreter import CodeInterpreterSkill
from core.telemetry import TelemetryCollector
from core.learning import LearningEngine
from core.faiss_memory import HybridMemoryBackend
from core.react_agent import ReActAgent
from core.mcp_support import MCPManager
from core.evaluation import Evaluator
from skills.telemetry_skill import TelemetrySkill
from skills.learning_skill import LearningSkill
from skills.eval_skill import EvalSkill
from skills.ruview_sensor import RuViewSensorSkill
from skills.presence_tracker import PresenceTrackerSkill
from skills.iphone_skill import IPhoneSkill
from skills.weather_skill import WeatherSkill
from skills.timer_skill import TimerSkill

logger = logging.getLogger(__name__)


@dataclass
class JarvisContext:
    """Holds all initialized components."""
    settings: object
    memory: Memory
    event_bus: EventBus
    metrics: MetricsCollector
    audit_log: AuditLog
    permission_gate: PermissionGate
    memory_manager: MemoryManager
    planner: Planner
    progress: ProgressReporter
    registry: SkillRegistry
    orchestrator: Orchestrator
    model_router: ModelRouter
    branch_manager: BranchManager
    notifications: NotificationManager
    automation_engine: AutomationEngine
    skill_store: SkillStore
    scheduler: Scheduler
    awareness: EnvironmentAwareness
    smart_runner: SmartRoutineRunner
    action_journal: ActionJournal
    pattern_learner: PatternLearner
    feedback_loop: FeedbackLoop
    network_presence: NetworkPresence
    calendar: CalendarAwareness
    proactive_engine: ProactiveEngine
    telemetry: TelemetryCollector
    learning: LearningEngine
    hybrid_memory: HybridMemoryBackend
    react_agent: ReActAgent
    mcp_manager: MCPManager
    evaluator: Evaluator


async def bootstrap() -> JarvisContext:
    """Initialize all Jarvis components and return a context object."""
    from dotenv import load_dotenv
    from config.settings import PROJECT_ROOT

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = get_settings()

    memory = Memory()
    await memory.init()
    logger.info("Memory initialized")

    if settings.memory_conversations_keep_days > 0 or settings.memory_episodic_keep_days > 0:
        await memory.prune(
            conversations_keep_days=settings.memory_conversations_keep_days,
            episodic_keep_days=settings.memory_episodic_keep_days,
            embeddings_keep_days=settings.memory_embeddings_keep_days,
        )

    event_bus = EventBus()
    metrics = MetricsCollector()

    audit_log = AuditLog()
    await audit_log.init()
    logger.info("Audit log initialized")

    permission_gate = PermissionGate(
        audit_log=audit_log,
        safe_mode=settings.safe_mode,
        dry_run=settings.dry_run,
        auto_approve_external=settings.auto_approve_external,
    )

    embedding_engine = EmbeddingEngine(db=memory._db)
    memory_manager = MemoryManager(memory=memory, embeddings=embedding_engine)

    # ── Telemetry ──
    telemetry = TelemetryCollector()
    if settings.telemetry_enabled:
        await telemetry.init()
        logger.info("Telemetry initialized")

    # ── Learning engine ──
    learning = LearningEngine()
    if settings.learning_enabled:
        await learning.init()
        logger.info("Learning engine initialized")

    # ── FAISS + Hybrid memory ──
    hybrid_memory = HybridMemoryBackend()
    if settings.faiss_enabled:
        try:
            count = await hybrid_memory.build_from_db(memory._db, embedding_engine)
            logger.info("Hybrid memory: indexed %d docs", count)
        except Exception as e:
            logger.warning("Hybrid memory init failed (brute-force fallback): %s", e)

    planner = Planner(event_bus=event_bus)
    progress = ProgressReporter(event_bus=event_bus)
    notifications = NotificationManager(event_bus=event_bus)
    model_router = ModelRouter(settings)
    branch_manager = BranchManager()

    registry = SkillRegistry()
    registry.register(OnboardingSkill())
    registry.register(SoulSetupSkill())
    registry.register(CrealityPrintSkill())
    registry.register(CrealityAPISkill())
    registry.register(ModelDownloaderSkill())
    registry.register(AppointmentBookerSkill())
    registry.register(SpotifySkill())
    registry.register(CodeWriterSkill())
    registry.register(SystemControlSkill())
    registry.register(SmartHomeSkill())
    registry.register(IPhoneSkill())
    registry.register(RuViewSensorSkill(registry=registry))
    registry.register(PresenceTrackerSkill(registry=registry))
    registry.register(AppleTVSkill())
    registry.register(SelfImproveSkill(registry))
    registry.register(MemorySkill(memory_manager))
    registry.register(IntrospectSkill())
    registry.register(WebResearchSkill())
    registry.register(ScreenReaderSkill())
    registry.register(FileManagerSkill())
    registry.register(DocumentRAGSkill())
    registry.register(BrowserAgentSkill())
    registry.register(CodeInterpreterSkill(prefer_docker=settings.code_interpreter_prefer_docker))
    registry.register(TimerSkill(event_bus))

    dynamic_count = load_dynamic_skills(registry)
    if dynamic_count:
        logger.info("Loaded %d dynamic skill(s)", dynamic_count)

    skill_store = SkillStore(registry=registry)
    skill_store.sync_with_registry()
    for meta in skill_store.get_all():
        if not meta.get("enabled", True):
            registry.unregister(meta["name"])

    orchestrator = Orchestrator(
        registry=registry,
        memory=memory,
        event_bus=event_bus,
        metrics=metrics,
        permission_gate=permission_gate,
        audit_log=audit_log,
        memory_manager=memory_manager,
        planner=planner,
    )
    orchestrator.model_router = model_router
    orchestrator.branch_manager = branch_manager
    orchestrator.skill_store = skill_store
    orchestrator.notifications = notifications
    orchestrator.telemetry = telemetry
    orchestrator.learning = learning
    orchestrator.hybrid_memory = hybrid_memory

    # ── ReAct agent (for complex multi-step reasoning) ──
    react_agent = ReActAgent(
        registry=registry,
        settings=settings,
        permission_gate=permission_gate,
        audit_log=audit_log,
    )
    orchestrator.react_agent = react_agent

    # ── MCP Manager ──
    mcp_manager = MCPManager()

    # ── Evaluator ──
    evaluator = Evaluator(orchestrator=orchestrator, registry=registry)

    # ------------------------------------------------------------------
    # Awareness sub-systems
    # ------------------------------------------------------------------

    action_journal = ActionJournal()
    logger.info("Action journal loaded (%d entries)", len(action_journal.get_today()))

    pattern_learner = PatternLearner()
    logger.info("Pattern learner ready (%d patterns)", len(pattern_learner.get_patterns(min_confidence=0.3)))

    feedback_loop = FeedbackLoop()
    logger.info("Feedback loop ready")

    network_presence = NetworkPresence()
    logger.info("Network presence ready (%d known devices)", len(network_presence.list_devices()))

    calendar = CalendarAwareness()
    logger.info("Calendar ready (%d events today)", len(calendar.get_today()))

    # Awareness layer — plugs everything together
    awareness = EnvironmentAwareness(registry=registry, memory_manager=memory_manager)
    awareness.action_journal = action_journal
    awareness.pattern_learner = pattern_learner
    awareness.feedback_loop = feedback_loop
    awareness.network_presence = network_presence
    awareness.calendar = calendar

    orchestrator.awareness = awareness
    logger.info("Orchestrator ready (with full environment awareness)")

    # Smart routine runner — routines go through the LLM
    smart_runner = SmartRoutineRunner(orchestrator=orchestrator, awareness=awareness)
    MORNING_ROUTINE.fallback_func = morning_routine
    smart_runner.register(MORNING_ROUTINE)
    EVENING_ROUTINE.fallback_func = evening_routine
    smart_runner.register(EVENING_ROUTINE)

    # Proactive engine — periodically suggests things to the user
    proactive_engine = ProactiveEngine(
        memory_manager,
        awareness=awareness,
        notifications=notifications,
    )
    orchestrator.proactive_engine = proactive_engine

    scheduler = Scheduler()
    scheduler.load()
    scheduler.register_routine("morning_routine", lambda: smart_runner.run("morning_routine"))
    scheduler.register_routine("evening_routine", lambda: smart_runner.run("evening_routine"))
    if not scheduler.list_schedules():
        scheduler.add_schedule(Schedule(
            name="morning",
            routine="morning_routine",
            hour=11,
            minute=0,
            days=[6, 0, 1, 2, 3, 4],  # Sun-Fri, skip Shabbat (5=Saturday)
            enabled=True,
        ))
        scheduler.add_schedule(Schedule(
            name="evening",
            routine="evening_routine",
            hour=23,
            minute=0,
            days=[6, 0, 1, 2, 3, 4],  # Sun-Fri, skip Shabbat (5=Saturday)
            enabled=True,
        ))
        logger.info("Scheduler: created default morning (11:00) and evening (23:00) schedules")
    registry.register(SchedulerSkill(scheduler))
    registry.register(CalendarSkill(calendar))
    registry.register(PresenceSkill(network_presence))
    registry.register(RestartSkill())
    registry.register(AutoRepairSkill(registry))
    registry.register(TelemetrySkill(telemetry))
    registry.register(LearningSkill(learning))
    registry.register(EvalSkill(evaluator))
    registry.register(WeatherSkill())
    logger.info("Scheduler ready with %d schedule(s)", len(scheduler.list_schedules()))

    automation_engine = AutomationEngine(
        event_bus=event_bus,
        registry=registry,
        notifications=notifications,
    )
    automation_engine.subscribe_all()
    skill_store.sync_with_registry()

    return JarvisContext(
        settings=settings,
        memory=memory,
        event_bus=event_bus,
        metrics=metrics,
        audit_log=audit_log,
        permission_gate=permission_gate,
        memory_manager=memory_manager,
        planner=planner,
        progress=progress,
        registry=registry,
        orchestrator=orchestrator,
        model_router=model_router,
        branch_manager=branch_manager,
        notifications=notifications,
        automation_engine=automation_engine,
        skill_store=skill_store,
        scheduler=scheduler,
        awareness=awareness,
        smart_runner=smart_runner,
        action_journal=action_journal,
        pattern_learner=pattern_learner,
        feedback_loop=feedback_loop,
        network_presence=network_presence,
        calendar=calendar,
        proactive_engine=proactive_engine,
        telemetry=telemetry,
        learning=learning,
        hybrid_memory=hybrid_memory,
        react_agent=react_agent,
        mcp_manager=mcp_manager,
        evaluator=evaluator,
    )


async def shutdown(ctx: JarvisContext):
    """Clean shutdown of all components — includes episodic session end."""
    if ctx.memory_manager and ctx.memory_manager.session_active:
        try:
            await ctx.memory_manager.end_session(ctx.orchestrator.conversation)
        except Exception as e:
            logger.warning("Session summary on shutdown failed: %s", e)
    await ctx.scheduler.stop()
    # Shutdown new subsystems
    try:
        await ctx.telemetry.close()
    except Exception:
        pass
    try:
        await ctx.learning.close()
    except Exception:
        pass
    try:
        await ctx.mcp_manager.shutdown()
    except Exception:
        pass
    # Flush awareness sub-systems
    try:
        await ctx.action_journal.flush()
    except Exception:
        pass
    try:
        ctx.pattern_learner.analyze()
    except Exception:
        pass
    await ctx.metrics.flush()
    await ctx.audit_log.close()
    await ctx.memory.close()
    logger.info("Jarvis shutdown complete")
