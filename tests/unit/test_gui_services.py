from datetime import date

from gnssgo.gui.models.tasks import GuiTaskState, GuiTaskType
from gnssgo.gui.services.map_service import station_marker_class, station_to_json
from gnssgo.gui.services.settings_service import SettingsService
from gnssgo.gui.services.task_service import TaskService
from gnssgo.models import DownloadPlan, RemoteFile, Station


def test_station_json_serialization() -> None:
    station = Station(
        id="WUH200CHN",
        marker="WUH2",
        latitude=30.5,
        longitude=114.3,
        country="CHN",
        network=["igs"],
        providers=["whu"],
    )
    payload = station_to_json(station)
    assert payload["id"] == "WUH200CHN"
    assert payload["providers"] == ["whu"]


def test_station_marker_classes_distinguish_igs_regional_and_overlap() -> None:
    igs = Station(id="IGS000AAA", data_networks=["igs"])
    regional = Station(
        id="REG000AAA",
        data_networks=["canada"],
        regional_sources=["cacs_ca"],
    )
    overlap = Station(
        id="BOTH00AAA",
        data_networks=["igs", "canada"],
        regional_sources=["cacs_ca"],
    )

    assert station_marker_class(igs) == "igs_only"
    assert station_marker_class(regional) == "regional_only"
    assert station_marker_class(overlap) == "igs_only"
    assert station_to_json(overlap)["marker_class"] == "igs_only"


def test_task_state_transition() -> None:
    service = TaskService(core=None)
    task = service.create_task(
        name="Test OBS",
        task_type=GuiTaskType.OBS,
        request={"stations": ["WUH200CHN"]},
    )
    plan = DownloadPlan(
        remote_files=[
            RemoteFile(
                provider="whu",
                url="ftp://example.test/file.rnx.gz",
                filename="file.rnx.gz",
                data_type="obs",
                date=date(2026, 8, 1),
            )
        ]
    )
    service.attach_plan(task, plan)
    assert task.state == GuiTaskState.READY
    assert task.total_files == 1
    service.mark_downloading(task)
    assert task.state == GuiTaskState.DOWNLOADING
    service.complete_from_results(task, [])
    assert task.state == GuiTaskState.COMPLETED


def test_settings_service_priorities_are_separate() -> None:
    service = SettingsService()
    assert service.provider_priority()[0] == "whu"
    assert service.product_provider_priority()[0] == "esa"


def test_product_interval_options_follow_product_type() -> None:
    from gnssgo import GNSSGo
    from gnssgo.gui.services.core_service import CoreService

    core = CoreService(GNSSGo())
    assert core.product_interval_options(
        product_type="orbit",
        day="2026-08-14",
        center="auto",
        tier="auto",
        system="auto",
    ) == [("15 min", "15M")]
    assert core.product_interval_options(
        product_type="clock",
        day="2026-08-14",
        center="auto",
        tier="auto",
        system="auto",
    ) == [("5 min", "05M"), ("30 s", "30S")]
    assert core.product_interval_options(
        product_type="ionex",
        day="2026-08-14",
        center="auto",
        tier="auto",
        system="auto",
    ) == [("2 h", "02H"), ("1 h", "01H")]
