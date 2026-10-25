"""Cycle 1 research orchestration, invoked by LeadGenerationEngine.

Every I/O operation is explicit and injectable. No outbound communication.
"""
from __future__ import annotations
from datetime import timedelta
from urllib.parse import urlsplit, urljoin
from collections import Counter
from pydantic import ValidationError
from evorove_lead.business_dna import BusinessDNA, DNA_CACHE_KIND
from evorove_lead.evidence import EvidenceBundle, EvidenceDraft, SourceSnapshot, digest, utcnow, company_key
from evorove_lead.market_intelligence import (
    BusinessDNAEngine, MarketResearchEngine, MarketIntelligenceReport, StructuredReasoner,
    ResearchUnavailable, InvalidStructuredCompletion, validate_supplied_sources,
    safe_validation_summary, format_validation_stop_reason, safe_runtime_reason,
)
from evorove_lead.search_planner import ICPHypothesisEngine, SearchPlanner, buyer_queries_for, is_buyer_query
from evorove_lead.qualification import LeadQualificationEngine, AdversarialValidation, QualificationGate, Decision
from evorove_lead.opportunity_ranking import CommercialOpportunityRanker
from evorove_lead.research_budget import ResearchBudget, BudgetLimits, BudgetedSearch, DailyBudgetStore, BudgetExhausted
from evorove_lead.research_cache import ResearchCache
from evorove_lead.research_reflection import ResearchReflectionLoop
from evorove_lead.adaptive_search import AdaptiveSearchPlanner, StrategyObservation
from evorove_lead.research_delivery import ResearchDelivery
from evorove_lead.run_ownership import CURRENT, OwnershipLost, check_ownership, guarded_call
from evorove_lead.intelligence_store import IntelligenceStore
from evorove_lead.llm_usage import BudgetedCompletion, LLMUsageLedger
from evorove_lead.web_search import fetch_source_snapshot, SearchError
from evorove_lead.web_people_search import WebSearchPeopleSearch
from evorove_lead.presence import PresenceRejected
from evorove_lead.platforms import is_platform
from evorove_lead.warehouse import new_id, CandidateRecord, HypothesisRecord, TraceRecord, RejectedTraceRecord
from evorove_lead.offer_reader import read_offer
from evorove_lead.offer import OfferRejected
from evorove_lead.candidate import Candidate
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.crm_touch import assembled_touch, stable_person_id, RecordingLeadTouchSink, OutboxCrmLeadTouchSink

class ResearchPipeline:
    def __init__(self, db_engine, search_client, completion, *, limits=None, fetcher=None,
                 clock=utcnow, dry_run=True, model="configured", max_output_tokens=4000, input_rate=3., output_rate=15.,
                 effort="low", qualify_effort="medium", output_tokens_by_stage=None):
        from evorove_lead.research_config import require_research_effort, DEFAULT_OUTPUT_TOKENS_BY_STAGE, output_tokens_for_stage
        self.store = IntelligenceStore(db_engine)
        self.cache = ResearchCache(db_engine, clock)
        self.delivery = ResearchDelivery(db_engine)
        self.daily = DailyBudgetStore(db_engine)
        self.ledger = LLMUsageLedger(db_engine)
        self.client, self.completion, self.fetcher = search_client, completion, fetcher or fetch_source_snapshot
        self.limits, self.clock, self.dry_run = limits or BudgetLimits(), clock, dry_run
        self.model, self.max_output_tokens = model, max_output_tokens
        self.output_tokens_by_stage = dict(output_tokens_by_stage or DEFAULT_OUTPUT_TOKENS_BY_STAGE)
        self.input_rate, self.output_rate = input_rate, output_rate
        # low can weaken candidate judgment. Qualification stays on medium.
        self.effort = require_research_effort(effort, name="effort")
        self.qualify_effort = require_research_effort(qualify_effort, name="qualify_effort")
        self.last_run = None
        self._tokens_for = lambda stage: output_tokens_for_stage(stage, self.output_tokens_by_stage, self.max_output_tokens)

    def generate(self, engine, seed, *, extra_hypotheses=()):
        from evorove_lead.engine import GenerationResult, GenerationStatus, RejectedHit, _brief_record
        if not seed.business_id:
            raise ValueError("research requires a tenant id and persistent warehouse")
        if not self.dry_run and not isinstance(engine._lead_touch_sink, OutboxCrmLeadTouchSink):
            raise ResearchUnavailable("live admission requires the existing outbox CRM sink")
        if getattr(engine._warehouse, "database_engine", None) is not self.store.engine:
            raise ResearchUnavailable("research and warehouse must use the same database engine")
        owner = CURRENT.get()
        if owner is None:
            raise ResearchUnavailable("research requires run ownership")
        run_id = owner.run_id
        now = self.clock()
        state = {"id": run_id, "started_at": now.isoformat(), "dry_run": self.dry_run, "status": "running", "stop_reason": "", "stages": [], "limits": self.limits.model_dump(), "usage": {}, "people_queries": 0, "people_search": []}
        self.last_run = state
        def save(): self.store.save(seed.business_id, "run", run_id, state)
        def stage(name): state["stages"].append(name); save()
        def record_usage(usage): state["usage"] = usage; save()
        budget = ResearchBudget(seed.business_id, self.limits, daily_store=self.daily, clock=self.clock, recorder=record_usage)
        budgeted = dict(completion=self.completion, budget=budget, ledger=self.ledger, run_id=run_id, model=self.model,
                        max_output_tokens=self.max_output_tokens, input_usd_per_million=self.input_rate,
                        output_usd_per_million=self.output_rate)
        reasoner = StructuredReasoner(BudgetedCompletion(**budgeted, effort=self.effort), output_tokens_for=self._tokens_for)
        qualify_reasoner = StructuredReasoner(BudgetedCompletion(**budgeted, effort=self.qualify_effort), output_tokens_for=self._tokens_for)
        search = BudgetedSearch(self.client, budget)
        offer = None
        accepted, handoffs, rejected = [], [], []
        seen_subjects = set()
        subject_sources = {}
        evaluated = set()
        seen_contacts = set()
        ranked = []
        def retrieve(url, subject):
            key = self.cache.key("source-1", url)
            cached = self.cache.get(seed.business_id, key)
            if cached:
                source = SourceSnapshot.model_validate(cached)
                return source if source.subject == subject else source.model_copy(update={"subject": subject, "id": digest(source.id + subject)})
            budget.consume(pages=1)
            try: source = guarded_call(self.fetcher, url, seed.business_id, subject)
            except PresenceRejected: return None
            budget.check_time()
            self.cache.put(seed.business_id, key, source.model_dump(mode="json"), timedelta(days=1))
            return source
        try:
            save()
            stage("business_analysis")
            # Reuse PresenceSource (including its optional browser fallback). Reserve
            # the maximum one-page/one-browser load before calling an opaque port.
            budget.consume(pages=1, browser_calls=1)
            materials = guarded_call(engine._presence.load, seed)
            offer = read_offer(materials, require_audience=False)
            sources = tuple(SourceSnapshot(id=digest(seed.site_url + m.name + m.body + now.isoformat()), business_id=seed.business_id,
                url=seed.site_url, final_url=seed.site_url, content=m.body, fetched_at=now,
                source_type="first_party" if m.name == seed.site_url else "owner_material",
                public=m.name == seed.site_url, material_name=m.name,
                subject=seed.business_id) for m in materials)
            additional_business_sources = tuple(source for path in ("/services", "/about") if (source := retrieve(urljoin(seed.site_url, path), seed.business_id)) is not None)
            sources = (*sources, *additional_business_sources)
            dna_key = self.cache.key(DNA_CACHE_KIND, seed.site_url, self.model, *(s.content_hash for s in sources))
            cached = self.cache.get(seed.business_id, dna_key)
            dna = BusinessDNA.model_validate(cached) if cached else BusinessDNAEngine(reasoner).analyze(seed.business_id, seed.site_url, sources)
            dna.validate_grounding()
            if not cached: self.cache.put(seed.business_id, dna_key, dna.model_dump(mode="json"), timedelta(days=30))
            self.store.save_evidence(dna.evidence)
            self.store.save(seed.business_id, "dna", dna.version, dna.model_dump(mode="json"))
            state["dna_version"] = dna.version
            brief_id = new_id("brief")
            engine._warehouse.save_brief(_brief_record(brief_id, seed.business_id, offer, now, seed.business_archetype))
            stage("market_research")
            # Market conclusions guide research, never substitute for a subject's need.
            hints = tuple(h.query_template for h in extra_hypotheses)
            market_key = self.cache.key("market-1-integrated", self.model, dna.model_dump_json(), *hints)
            cached_market = self.cache.get(seed.business_id, market_key)
            market = MarketIntelligenceReport.model_validate(cached_market) if cached_market else MarketResearchEngine(reasoner, search, retrieve).research(dna, research_hints=hints)
            market.validate_grounding(seed.business_id, market.evidence.sources)
            if not cached_market:
                self.cache.put(seed.business_id, market_key, market.model_dump(mode="json"), timedelta(days=7))
            self.store.save_evidence(market.evidence)
            market_id = digest(market.model_dump_json())
            self.store.save(seed.business_id, "market", market_id, market.model_dump(mode="json"))
            state["market_document_id"] = market_id
            state["market_cache_hit"] = cached_market is not None
            stage("hypothesis_generation")
            proposed = ICPHypothesisEngine(reasoner).generate(dna, market=market)
            hypotheses = []
            for priority, proposed_h in enumerate(proposed, 1):
                content_version = digest(proposed_h.model_dump_json(exclude={"version"}))
                prior = self.store.get(seed.business_id, "icp_version", proposed_h.stable_key)
                version = prior["version"] if prior and prior["content_hash"] == content_version else prior["version"] + 1 if prior else 1
                h = proposed_h.model_copy(update={"version": version})
                self.store.save(seed.business_id, "icp_version", h.stable_key, {"content_hash": content_version, "version": version, "initial_rank": priority, "rank_reason": "supporting evidence count, then uncalibrated research confidence"})
                self.store.save(seed.business_id, "icp_proposed", h.stable_key, h.model_dump(mode="json"))
                hypotheses.append(h)
            observations = []
            previous = self.store.list(seed.business_id, "strategy_observation")
            from evorove_lead.reweight_hypotheses import strategy_feedback
            feedback = strategy_feedback(engine._warehouse, self.store, seed.business_id, now=now)
            for h in hypotheses:
                matching = [r for r in previous if r["key"] == h.stable_key]
                observations.append(StrategyObservation(h.stable_key, discovered=sum(r["discovered"] for r in matching), qualified=sum(r["qualified"] for r in matching), sources=sum(r["sources"] for r in matching), reliable_sources=sum(r["reliable_sources"] for r in matching), cost_microusd=sum(r["cost_microusd"] for r in matching), independent_cohorts=len({r["cohort"] for r in matching}), mature_closed=feedback.get(h.stable_key, {}).get("done", 0), mature_resolved=feedback.get(h.stable_key, {}).get("resolved", 0)))
            allocation = AdaptiveSearchPlanner().allocate(observations, max(0, self.limits.queries - budget.used["queries"]))
            for h in hypotheses:
                if allocation[h.stable_key] == 0: continue
                hypothesis_id = new_id("hypothesis")
                self.store.save(seed.business_id, "icp", digest(h.stable_key + str(h.version) + dna.version), {**h.model_dump(mode="json"), "stable_key": h.stable_key, "dna_version": dna.version})
                planned_queries = buyer_queries_for(h)
                engine._warehouse.save_hypothesis(HypothesisRecord(id=hypothesis_id, business_id=seed.business_id, brief_id=brief_id, audience_segment=h.segment, channel="web_search", query_template=planned_queries[0] if planned_queries else "", intent_trigger="need_statement", status="live", fit_score=0, evidence_score=0, reach_estimate=0, created_at=now))
                self.store.save(seed.business_id, "hypothesis_attribution", hypothesis_id, {"stable_key": h.stable_key, "cohort": now.date().isoformat(), "run_id": run_id, "hypothesis_id": hypothesis_id})
                stats = {"key": h.stable_key, "cohort": now.date().isoformat(), "discovered": 0, "qualified": 0, "sources": 0, "reliable_sources": 0, "cost_microusd": 0}
                initial_cost = budget.used["cost_microusd"]
                query_count = 0
                hypothesis_subjects = set()
                def execute(queries):
                    nonlocal query_count
                    reasons = Counter()
                    qualified_count = 0
                    stage("prospect_search")
                    for query in queries:
                        if not is_buyer_query(query):
                            continue
                        if query_count >= allocation[h.stable_key]: break
                        query_count += 1
                        state["people_queries"] = state.get("people_queries", 0) + 1
                        # Discovery mode reuses the existing per-hypothesis connector,
                        # but defers contact/LLM work until company deduplication.
                        connector = WebSearchPeopleSearch(search)
                        attempt = {"hypothesis_key": h.stable_key, "query": query, "hits": None, "error": None, "rejections": {}}
                        state["people_search"].append(attempt)
                        save()
                        try:
                            discovered = connector.discover(SearchPlanner.adapt(h, query))
                        except SearchError as exc:
                            attempt["error"] = exc.code
                            save()
                            continue
                        attempt["hits"] = len(discovered)
                        save()
                        for hit in discovered:
                            trace_id = new_id("trace")
                            engine._warehouse.save_trace(TraceRecord(id=trace_id, business_id=seed.business_id, hypothesis_id=hypothesis_id, url=hit.url, fetched_at=self.clock(), raw_text=hit.snippet, query_used=query, source_channel="search_snippet"))
                            def reject_trace(code):
                                reasons[code] += 1
                                attempt["rejections"][code] = attempt["rejections"].get(code, 0) + 1
                                save()
                                engine._warehouse.save_rejected_trace(RejectedTraceRecord(id=new_id("rejected_trace"), business_id=seed.business_id, trace_id=trace_id, reason=code, rejected_at=self.clock()))
                            try: domain = company_key(hit.url)
                            except ValueError:
                                reject_trace("invalid_url")
                                continue
                            if domain == company_key(seed.site_url):
                                reject_trace("own_site_or_platform")
                                continue
                            host = urlsplit(hit.url).hostname or ""
                            if host == "linkedin.com" or host.endswith(".linkedin.com"):
                                reject_trace("restricted_source")
                                continue
                            public_post = is_platform(domain)
                            self.store.discover(seed.business_id, hit.url, h.stable_key)
                            subject = hit.url if public_post or h.buyer_type != "company" else domain
                            kind = "person" if public_post else h.buyer_type
                            prior_sources = subject_sources.get(subject, ())
                            if subject not in seen_subjects:
                                budget.consume(companies=1)
                                seen_subjects.add(subject)
                            elif any(s.url == hit.url for s in prior_sources) and (subject, h.stable_key) in evaluated:
                                reject_trace("duplicate_subject")
                                continue
                            if subject not in hypothesis_subjects:
                                hypothesis_subjects.add(subject)
                                stats["discovered"] += 1
                            stage("evidence_collection")
                            urls = [hit.url]
                            if kind == "company":
                                from evorove_lead.decision_maker import _candidate_urls
                                urls = list(_candidate_urls(hit.url))
                            new_sources = tuple(s for url in urls if url not in {p.url for p in prior_sources} and (s := retrieve(url, subject)) is not None)
                            prospect_sources = (*prior_sources, *new_sources)
                            subject_sources[subject] = prospect_sources
                            evaluated.add((subject, h.stable_key))
                            stats["sources"] += len(prospect_sources)
                            if not prospect_sources:
                                reject_trace("no_sources")
                                continue
                            # Contact syntax and existing decision-maker heuristics cost no LLM call.
                            from evorove_lead.crm_touch import EMAIL_RE
                            from evorove_lead.web_people_search import _extract_phone
                            from evorove_lead.decision_maker import heuristic_decision_maker
                            if not any(EMAIL_RE.search(s.content) or _extract_phone(s.content) for s in prospect_sources):
                                reject_trace("no_published_contact")
                                continue
                            hint = heuristic_decision_maker([(s.final_url, s.content) for s in prospect_sources], domain) if kind == "company" else None
                            extraction_key = self.cache.key("extraction-1", self.model, dna.version, h.stable_key, *(s.content_hash for s in prospect_sources))
                            cached_bundle = self.cache.get(seed.business_id, extraction_key)
                            if cached_bundle:
                                bundle = EvidenceBundle.model_validate(cached_bundle)
                            else:
                                draft = reasoner.ask("Extract claims and a real appropriate person from the supplied sources. Preserve tenant and subject. Bind name, role and published address to that person. Need claims must describe this subject's situation. Do not derive dates from retrieval time.", EvidenceDraft, {"business_id": seed.business_id, "subject": subject, "subject_kind": kind, "hypothesis": h.model_dump(mode="json"), "contact_hint": hint.__dict__ if hint else None, "sources": [s.model_dump(mode="json") for s in prospect_sources]})
                                bundle = draft.bind(prospect_sources)
                                validate_supplied_sources(bundle, prospect_sources)
                                self.cache.put(seed.business_id, extraction_key, bundle.model_dump(mode="json"), timedelta(days=1))
                            if bundle.business_id != seed.business_id or bundle.subject != subject or bundle.subject_kind != kind: raise ValueError("candidate evidence identity mismatch")
                            if bundle.contact and (bundle.contact.identity.casefold() in seen_contacts or self.delivery.seen(seed.business_id, bundle.contact.identity)):
                                reject_trace("duplicate_contact")
                                continue
                            self.store.save_evidence(bundle)
                            first = prospect_sources[0]
                            engine._warehouse.save_trace(TraceRecord(id=trace_id, business_id=seed.business_id, hypothesis_id=hypothesis_id, url=first.final_url, fetched_at=first.fetched_at, raw_text=first.content, query_used=query, source_channel="web_search"))
                            stage("qualification")
                            from evorove_lead.qualification import QualificationAssessment, AdversarialReview
                            review_key = self.cache.key("qualification-2-market", self.model, dna.version, market_id, h.stable_key, bundle.model_dump_json())
                            cached_review = self.cache.get(seed.business_id, review_key)
                            if cached_review:
                                assessment = QualificationAssessment.model_validate(cached_review["assessment"])
                                stage("adversarial_review")
                                review = AdversarialReview.model_validate(cached_review["review"])
                            else:
                                assessment = LeadQualificationEngine(qualify_reasoner).qualify(dna, h, bundle, market=market)
                                stage("adversarial_review")
                                review = AdversarialValidation(qualify_reasoner).review(dna, h, bundle, assessment, market=market)
                                self.cache.put(seed.business_id, review_key, {"assessment": assessment.model_dump(mode="json"), "review": review.model_dump(mode="json")}, timedelta(days=1))
                            # Additional evidence is collected only inside the same run budget.
                            if review.additional_queries:
                                extra = list(prospect_sources)
                                for q in review.additional_queries:
                                    for result in search.search(q):
                                        if company_key(result.url) == domain:
                                            source = retrieve(result.url, subject)
                                            if source and source.id not in {s.id for s in extra}: extra.append(source)
                                if len(extra) > len(prospect_sources):
                                    draft = reasoner.ask("Re-extract evidence after adversarial requests, preserving uncertainty and exact source IDs.", EvidenceDraft, {"business_id": seed.business_id, "subject": subject, "subject_kind": h.buyer_type, "sources": [s.model_dump(mode="json") for s in extra]})
                                    bundle = draft.bind(extra)
                                    validate_supplied_sources(bundle, extra)
                                    self.store.save_evidence(bundle)
                                    assessment = LeadQualificationEngine(qualify_reasoner).qualify(dna, h, bundle, market=market)
                                    review = AdversarialValidation(qualify_reasoner).review(dna, h, bundle, assessment, market=market)
                            stage("qualification_gate")
                            identity = bundle.contact.identity if bundle.contact else ""
                            gate = QualificationGate().evaluate(dna=dna, bundle=bundle, assessment=assessment, review=review, duplicate=self.delivery.seen(seed.business_id, identity), now=self.clock(), research_remaining=(budget.iterations_remaining(h.stable_key) and query_count < allocation[h.stable_key]))
                            decision_id = new_id("qualification")
                            candidate_id = new_id("candidate")
                            self.store.save(seed.business_id, "qualification", decision_id, {"run_id": run_id, "candidate_id": candidate_id, "hypothesis_id": hypothesis_id, "dna_version": dna.version, "market_document_id": market_id, "evidence": bundle.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json"), "review": review.model_dump(mode="json"), "gate": gate.model_dump(mode="json")})
                            if gate.decision != Decision.QUALIFIED:
                                reasons.update(gate.codes)
                                for code in gate.codes:
                                    attempt["rejections"][code] = attempt["rejections"].get(code, 0) + 1
                                save()
                                engine._warehouse.save_candidate(CandidateRecord(id=candidate_id, business_id=seed.business_id, hypothesis_id=hypothesis_id, identity=identity, channel="email" if "@" in identity else "unknown", reason=gate.codes[0], reason_source=first.final_url, fit=assessment.product_fit.score or 0, evidence=assessment.need_evidence.score or 0, addressable=bool(identity), decision="rejected", decided_at=self.clock(), trace_id=trace_id, rejection_reason=gate.codes[0]))
                                rejected.append(RejectedHit(identity=identity, why=gate.codes[0]))
                                continue
                            rank = CommercialOpportunityRanker().rank(gate, assessment)
                            self.store.save(seed.business_id, "ranking", decision_id, rank.model_dump(mode="json"))
                            candidate = Candidate(identity=identity, reason=gate.reason, reason_source=gate.reason_source)
                            email = identity if "@" in identity else None
                            phone = identity if not email else None
                            person_id = stable_person_id(seed.business_id, phone=phone, email=email, identity=identity)
                            handoff = Cycle1Handoff.from_candidate(candidate, "email" if email else "phone", person_id=person_id, hypothesis_id=hypothesis_id)
                            record = CandidateRecord(id=candidate_id, business_id=seed.business_id, hypothesis_id=hypothesis_id, identity=identity, channel=handoff.channel, reason=gate.reason, reason_source=gate.reason_source, fit=assessment.product_fit.score or 0, evidence=assessment.need_evidence.score or 0, addressable=True, decision="cold", decided_at=self.clock(), trace_id=trace_id, email=email or "", phone=phone or "")
                            payload = assembled_touch(seed.business_id, candidate, handoff, segment=h.segment)
                            seen_contacts.add(identity.casefold())
                            ranked.append((rank.score, record, payload, candidate, handoff, h.stable_key, decision_id, tuple(c.id for c in bundle.claims)))
                            stats["qualified"] += 1
                            stats["reliable_sources"] += len(bundle.sources)
                            qualified_count += 1
                    return {"observation_id": new_id("observation"), "qualified": qualified_count, "rejections": dict(reasons), "infrastructure_failure": bool(search.errors)}
                def record_reflection(value): self.store.save(seed.business_id, "reflection", new_id("reflection"), {"run_id": run_id, "hypothesis_key": h.stable_key, **value})
                ResearchReflectionLoop(reasoner, budget, record_reflection).run(planned_queries[:1], execute, context=h.model_dump(mode="json"), iteration_scope=h.stable_key)
                stats["cost_microusd"] = budget.used["cost_microusd"] - initial_cost
                if not search.errors:
                    self.store.save(seed.business_id, "strategy_observation", digest(run_id + h.stable_key), stats)
            state["status"] = "completed"
        except SearchError as exc:
            state["status"] = "budget_exhausted" if exc.code == "search_request_limit" else "partial" if search.successes else "failed"
            state["stop_reason"] = exc.code
            state["failed_stage"] = state["stages"][-1]
        except BudgetExhausted as exc:
            state["status"], state["stop_reason"] = "budget_exhausted", str(exc)
        except PresenceRejected:
            failed_stage = state["stages"][-1] if state["stages"] else "business_analysis"
            if failed_stage == "business_analysis":
                state["status"], state["stop_reason"] = "failed", "brief_unavailable"
            else:
                state["status"], state["stop_reason"] = "failed", "PresenceRejected"
            state["failed_stage"] = failed_stage
        except ResearchUnavailable as exc:
            failed_stage = state["stages"][-1] if state["stages"] else "business_analysis"
            reason = str(exc).strip()[:200] or "ResearchUnavailable"
            if isinstance(exc, InvalidStructuredCompletion):
                self.ledger.record(seed.business_id, run_id, {"status": "validation_failed", **exc.summary})
                state["validation"] = exc.summary
            state["status"], state["stop_reason"] = "failed", reason
            state["failed_stage"] = failed_stage
        except (OfferRejected, ValidationError, ValueError) as exc:
            failed_stage = state["stages"][-1] if state["stages"] else "business_analysis"
            if isinstance(exc, ValidationError):
                summary = safe_validation_summary(exc, getattr(exc, "title", None) or "Model")
                self.ledger.record(seed.business_id, run_id, {"status": "validation_failed", **summary})
                state["validation"] = summary
                reason = format_validation_stop_reason(summary)
            else:
                reason = safe_runtime_reason(exc)
            state["status"], state["stop_reason"] = "failed", reason
            state["failed_stage"] = failed_stage
        except BaseException as exc:
            state["status"], state["stop_reason"] = ("failed", "dependency_failure") if isinstance(exc, Exception) else ("interrupted", "cancelled")
            raise
        finally:
            check_ownership()
            if search.errors:
                state["search_errors"] = [error.code for error in search.errors]
                if state["status"] != "budget_exhausted":
                    state["stop_reason"] = search.errors[-1].code
                if state["status"] not in {"failed", "budget_exhausted"}:
                    state["status"] = "budget_exhausted" if search.errors[-1].code == "search_request_limit" else "partial" if search.successes else "failed"
            try:
                if state["status"] in {"completed", "partial", "budget_exhausted"} or (state["status"] == "failed" and (search.errors or ranked)):
                    for _, record, payload, candidate, handoff, hypothesis_key, decision_id, claim_ids in sorted(ranked, key=lambda item: (-item[0], item[3].identity)):
                        if self.dry_run:
                            self.store.save(seed.business_id, "dry_run_qualified", record.id, {"hypothesis_id": record.hypothesis_id, "qualification_id": decision_id})
                        else:
                            if not self.delivery.admit(record, payload, hypothesis_key, run_id, claim_ids=claim_ids):
                                continue
                            stage("crm_cold")
                            guarded_call(engine._lead_touch_sink.publish, seed.business_id, payload)
                            handoffs.append(handoff)
                        accepted.append(candidate)
            except Exception:
                state["status"], state["stop_reason"] = "failed", "delivery_or_database_failure"
                raise
            finally:
                state["finished_at"] = self.clock().isoformat()
                state["qualified"] = len(accepted)
                state["handoffs"] = len(handoffs)
                save()
        people = int(state.get("people_queries") or 0)
        run_status = state["status"]
        if run_status in {"failed", "partial", "budget_exhausted"}:
            result_status = GenerationStatus(run_status)
        elif run_status == "completed" and people > 0:
            result_status = GenerationStatus.PEOPLE_FOUND if accepted else GenerationStatus.NO_FIT
        elif accepted:
            result_status = GenerationStatus.PARTIAL
        else:
            result_status = GenerationStatus.FAILED
        return GenerationResult(stop_reason=state.get("stop_reason", ""), status=result_status, offer=offer, candidates=tuple(accepted), handoffs=tuple(handoffs), rejected=tuple(rejected))
