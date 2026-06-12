from __future__ import annotations

from services.touch.touch_sensor_service import TouchSensorService


def test_disabled_touch_sensor_does_not_start():
    service = TouchSensorService(enabled=False)

    assert service.start(lambda: None) is False
    assert service.started is False

    service.close()
