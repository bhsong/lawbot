"""Map separately scored semantic pairs and assess a single candidate."""

from dataclasses import dataclass, replace

import numpy as np

from lawbot.api_client import ModelServerError
from lawbot.exact_cache import canonical, digest
from lawbot.semantic_policy import Condition, ReusePolicy, check_conditions
from lawbot.semantic_settings import Calibration, RerankTokens


@dataclass(frozen=True)
class Parent:
    snapshot_id: str
    chunk_id: str
    text_hash: str
    text: str

    @property
    def pair_id(self) -> str:
        return digest(canonical(['parent', self.chunk_id, self.text_hash, self.snapshot_id, digest(self.text)]))


@dataclass(frozen=True)
class Candidate:
    binding_id: str
    atom_revision: str
    question_revision: str
    question: str
    parent: Parent
    answer_id: str
    policy_id: str
    policy_version: str
    policy_json: str

    @property
    def question_pair_id(self) -> str:
        return digest(canonical(['question', self.atom_revision, self.question_revision, digest(self.question)]))


@dataclass(frozen=True)
class PairScores:
    parents: dict[str, float]
    questions: dict[str, float]
    truncated: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Assessment:
    reason: str
    candidate: Candidate | None = None
    linked_parent_score: float | None = None
    question_match_score: float | None = None
    margin: float | None = None
    condition: Condition | None = None

    @property
    def would_hit(self) -> bool:
        return self.reason == 'hit'


def assess(question: str, candidates: list[Candidate], scores: PairScores, calibration: Calibration, *, complete=True):
    if not candidates:
        return Assessment('no_match')
    expected = {item.question_pair_id for item in candidates}
    if expected - scores.questions.keys() - scores.truncated:
        return Assessment('score_contract_error')
    scored = [item for item in candidates if item.question_pair_id in scores.questions]
    if not scored:
        return Assessment('truncated_input' if scores.truncated else 'score_contract_error')
    ranked = sorted(scored, key=lambda item: (-scores.questions[item.question_pair_id], item.binding_id))
    best = ranked[0]
    competitor = next((item for item in ranked[1:] if item.answer_id != best.answer_id), None)
    question_score = scores.questions[best.question_pair_id]
    parent_score = scores.parents.get(best.parent.pair_id)
    margin = None if competitor is None else question_score - scores.questions[competitor.question_pair_id]
    result = Assessment('hit', best, parent_score, question_score, margin)
    if parent_score is None:
        return replace(
            result, reason='truncated_input' if best.parent.pair_id in scores.truncated else 'parent_score_missing'
        )
    if parent_score <= calibration.parent_threshold:
        return replace(result, reason='parent_score_low')
    if question_score <= calibration.question_threshold:
        return replace(result, reason='question_score_low')
    if (margin is None and not complete) or (margin is not None and margin <= calibration.min_answer_margin):
        return replace(result, reason='ambiguous_candidates')
    try:
        condition = check_conditions(question, ReusePolicy.model_validate_json(best.policy_json))
    except ValueError:
        return replace(result, reason='invalid_policy')
    return replace(result, reason='hit' if condition.reason == 'matched' else condition.reason, condition=condition)


def score_pairs(
    question: str,
    candidates: list[Candidate],
    client,
    calibration: Calibration,
    tokens: RerankTokens,
    *,
    before_request=None,
):
    truncated: set[str] = set()

    def fitting(pairs: dict[str, str], instruction: str) -> dict[str, str]:
        omitted = {key for key, doc in pairs.items() if not tokens.fits(instruction, question, doc)}
        truncated.update(omitted)
        return {key: doc for key, doc in pairs.items() if key not in omitted}

    def score(pairs: dict[str, str], instruction: str) -> dict[str, float]:
        if not pairs:
            return {}
        try:
            scores = client.rerank(
                calibration.reranker_model,
                question,
                list(pairs.values()),
                instruction,
                timeout=None if before_request is None else before_request(len(pairs)),
                strict=True,
            )
        except ValueError as exc:
            raise ModelServerError('시맨틱 리랭크 응답을 읽지 못했습니다.') from exc
        if len(scores) != len(pairs) or any(type(value) not in (float, int) or not 0 <= value <= 1 for value in scores):
            raise ModelServerError('시맨틱 리랭크 점수 계약 검증에 실패했습니다.')
        return dict(zip(pairs, scores, strict=True))

    questions = fitting({item.question_pair_id: item.question for item in candidates}, calibration.question_instruction)
    parents = fitting(
        {item.parent.pair_id: item.parent.text for item in candidates if item.question_pair_id in questions},
        calibration.parent_instruction,
    )
    parent_scores = score(parents, calibration.parent_instruction)
    question_scores = score(questions, calibration.question_instruction)
    return PairScores(parent_scores, question_scores, frozenset(truncated))


def valid_vectors(vectors, rows: int, dimension: int) -> bool:
    return (
        isinstance(vectors, np.ndarray)
        and vectors.dtype == np.float32
        and vectors.shape == (rows, dimension)
        and bool(np.isfinite(vectors).all())
        and bool(np.allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=0, atol=0.005))
    )
