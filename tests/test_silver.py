from eu_electricity_forecast.silver import silver_table_for_item


def test_known_data_items_route_to_typed_silver_tables():
    assert silver_table_for_item("day_ahead_prices") == "silver_prices"
    assert silver_table_for_item("actual_load") == "silver_load"
    assert silver_table_for_item("actual_generation_per_type") == "silver_generation"
    assert silver_table_for_item("day_ahead_wind_solar_forecast") == "silver_forecasts"
    assert silver_table_for_item("physical_flows") == "silver_cross_border_flows"


def test_unknown_data_item_has_no_silver_target():
    assert silver_table_for_item("future_data_item") is None
