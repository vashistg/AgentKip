"""Tests for episodic memory: log, retrieve, filter, time-range queries."""
import pytest
from datetime import datetime, timedelta

from memory.episodic import EpisodeType, get_episodes_since, get_recent_episodes, log_episode


ATHLETE_ID = "mem_test_athlete"


class TestLogAndRetrieve:
    def test_logged_episode_is_retrievable(self, engine):
        log_episode(
            athlete_id=ATHLETE_ID,
            episode_type=EpisodeType.plan_generated,
            data={"plan_id": "p1", "total_volume_km": 40.0},
        )
        episodes = get_recent_episodes(ATHLETE_ID)
        assert len(episodes) == 1
        assert episodes[0]["type"] == "plan_generated"
        assert episodes[0]["data"]["plan_id"] == "p1"

    def test_multiple_episodes_ordered_newest_first(self, engine):
        t_old = datetime.utcnow() - timedelta(hours=2)
        t_new = datetime.utcnow()
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,
                    data={"label": "old"}, event_date=t_old)
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,
                    data={"label": "new"}, event_date=t_new)
        episodes = get_recent_episodes(ATHLETE_ID)
        assert episodes[0]["data"]["label"] == "new"
        assert episodes[1]["data"]["label"] == "old"

    def test_limit_is_respected(self, engine):
        for i in range(5):
            log_episode(ATHLETE_ID, EpisodeType.workout_completed,
                        data={"index": i})
        episodes = get_recent_episodes(ATHLETE_ID, limit=3)
        assert len(episodes) == 3

    def test_returns_empty_for_unknown_athlete(self, engine):
        log_episode(ATHLETE_ID, EpisodeType.plan_generated, data={})
        assert get_recent_episodes("other_athlete") == []


class TestEpisodeTypeFilter:
    def test_filter_by_type(self, engine):
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,    data={"t": "plan"})
        log_episode(ATHLETE_ID, EpisodeType.injury_flagged,    data={"t": "injury"})
        log_episode(ATHLETE_ID, EpisodeType.workout_completed, data={"t": "workout"})

        plans = get_recent_episodes(ATHLETE_ID,
                                    episode_type=EpisodeType.plan_generated)
        assert len(plans) == 1
        assert plans[0]["data"]["t"] == "plan"

    def test_all_types_returned_when_no_filter(self, engine):
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,    data={})
        log_episode(ATHLETE_ID, EpisodeType.injury_flagged,    data={})
        all_episodes = get_recent_episodes(ATHLETE_ID)
        assert len(all_episodes) == 2


class TestGetEpisodesSince:
    def test_returns_only_episodes_after_cutoff(self, engine):
        now = datetime.utcnow()
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,
                    data={"label": "before"}, event_date=now - timedelta(hours=3))
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,
                    data={"label": "after"},  event_date=now - timedelta(hours=1))

        cutoff = now - timedelta(hours=2)
        episodes = get_episodes_since(ATHLETE_ID, since=cutoff)
        assert len(episodes) == 1
        assert episodes[0]["data"]["label"] == "after"

    def test_returns_empty_when_all_before_cutoff(self, engine):
        now = datetime.utcnow()
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,
                    data={}, event_date=now - timedelta(days=7))
        episodes = get_episodes_since(ATHLETE_ID, since=now - timedelta(days=1))
        assert episodes == []

    def test_since_with_type_filter(self, engine):
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=2)
        log_episode(ATHLETE_ID, EpisodeType.plan_generated,
                    data={"t": "plan"},   event_date=now - timedelta(hours=1))
        log_episode(ATHLETE_ID, EpisodeType.injury_flagged,
                    data={"t": "injury"}, event_date=now - timedelta(hours=1))

        plans = get_episodes_since(ATHLETE_ID, since=cutoff,
                                   episode_type=EpisodeType.plan_generated)
        assert len(plans) == 1
        assert plans[0]["data"]["t"] == "plan"
