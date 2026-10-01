"""Measured Q24 multiply-only versus MAC experiments."""

from tools.benchmark_q24 import benchmark


def test_q24_benchmark_measures_both_models_and_truncation():
    result = benchmark(samples=10_000)

    assert result["raw_multiply_only"]["cycles"] == 17
    assert result["raw_mac_accumulate"]["cycles"] == 11
    assert result["prescaled_multiply_only"]["cycles"] == 11
    assert result["prescaled_mac_accumulate"]["cycles"] == 8

    assert result["raw_mac_accumulate"]["cycles"] < result[
        "raw_multiply_only"
    ]["cycles"]
    assert result["prescaled_mac_accumulate"]["cycles"] < result[
        "prescaled_multiply_only"
    ]["cycles"]
    assert result["raw_multiply_only"]["result"] != result[
        "raw_mac_accumulate"
    ]["result"]
    assert result["truncation"]["mismatches"] > 0
    assert result["truncation"]["max_abs_lsb"] == 1
