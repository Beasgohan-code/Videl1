"""UTC-bucket regressions against mock Mongo, not real concurrency or telemetry."""
import csv
import io
from datetime import datetime, timezone

import pytest
from tests.harness import reset_db, run
from webapp import db_models, services_bots, services_clone_files as analytics


class Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    run(reset_db())
    db_models.reset_memory_collections()
    monkeypatch.setattr(analytics, 'datetime', Frozen)


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_rolling_day_covers_both_partial_utc_days_and_half_open_boundaries():
    async def go():
        coll=db_models.files_col()
        # End excluded; start included; previous period excludes start.
        records=[('prev_start','2026-10-31T12:00:00',1),('prev_end','2026-11-01T11:59:59',2),
                 ('current_start','2026-11-01T12:00:00',3),('current_end','2026-11-02T11:59:59',4),
                 ('end_excluded','2026-11-02T12:00:00',5),('future','2026-11-02T15:00:00',6)]
        for key,ts,size in records:
            await coll.insert_one({'file_uid':key,'workspace_id':'A','deleted':False,'bot_id':'default','uploaded_ts':stamp(ts),'size_bytes':size})
        await coll.insert_one({'file_uid':'other','workspace_id':'B','deleted':False,'bot_id':'default','uploaded_ts':stamp('2026-11-02T10:00:00'),'size_bytes':100})
        await coll.insert_one({'file_uid':'trash','workspace_id':'A','deleted':True,'bot_id':'default','uploaded_ts':stamp('2026-11-02T10:00:00'),'size_bytes':100})
        data=await analytics.compute_owner_and_bot_analytics('A',1,days=1,bot_id='default')
        assert data['totals']['stored_files']==6 and data['totals']['period_uploads']==2
        assert data['comparison_previous_period']['files_delta']==0
        assert data['complete_utc_buckets'] is True
        assert data['series']['days']==['2026-11-01','2026-11-02']
        assert data['series']['uploads']==[1,1] and data['series']['storage_bytes_added']==[3,4]
        rows=list(csv.reader(io.StringIO(analytics.export_analytics_csv(data))))
        assert rows==[['date','uploads','downloads','storage_bytes_added'],['2026-11-01','1','','3'],['2026-11-02','1','','4']]
    run(go())


def test_midnight_end_does_not_add_empty_extra_day(monkeypatch):
    class Midnight(Frozen):
        @classmethod
        def now(cls,tz=None):return cls(2026,11,2,0,0,tzinfo=timezone.utc)
    monkeypatch.setattr(analytics,'datetime',Midnight)
    data=run(analytics.compute_owner_and_bot_analytics('A',1,days=1))
    assert data['series']['days']==['2026-11-01']


def test_custom_range_uses_inclusive_utc_dates_and_exclusive_end():
    async def go():
        coll=db_models.files_col()
        for i,ts in enumerate(['2026-10-01T00:00:00','2026-10-02T23:59:59','2026-10-03T00:00:00']):
            await coll.insert_one({'file_uid':str(i),'workspace_id':'A','deleted':False,'uploaded_ts':stamp(ts),'size_bytes':1})
        data=await analytics.compute_owner_and_bot_analytics('A',1,start_date='2026-10-01',end_date='2026-10-02')
        assert data['range_days']==2 and data['totals']['period_uploads']==2
        assert data['series']['days']==['2026-10-01','2026-10-02'] and data['series']['uploads']==[1,1]
    run(go())


@pytest.mark.parametrize('start,end',[
    ('2026-01-01',''),('', '2026-01-01'),('not-a-date','2026-01-01'),('20260101','2026-01-02'),
    ('2026-01-02','2026-01-01'),('2025-01-01','2026-01-01'),('0001-01-01','0001-01-01'),
    ('9999-12-31','9999-12-31'),('2026-01-01T00:00:00','2026-01-02'),
])
def test_invalid_custom_ranges_are_not_silently_replaced(start,end):
    with pytest.raises(services_bots.SaaSValidationError):
        run(analytics.compute_owner_and_bot_analytics('A',1,start_date=start,end_date=end))
