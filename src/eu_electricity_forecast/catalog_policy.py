"""Validate the source catalog selected for an environment's data job."""


def validate_source_catalog(*, environment: str, output_catalog: str, input_catalog: str, production_catalog: str) -> None:
    """Reject unsupported input/output catalog combinations before a job writes."""
    if environment not in {"dev", "qa", "prod"}:
        raise ValueError(f"Unsupported environment: {environment}")
    if environment == "prod":
        if output_catalog != production_catalog or input_catalog != production_catalog:
            raise ValueError("Production jobs must read and write the production catalog.")
    else:
        if output_catalog == production_catalog:
            raise ValueError("Dev and QA jobs cannot write to the production catalog.")
        if input_catalog not in {output_catalog, production_catalog}:
            raise ValueError("Dev and QA jobs can read only their own catalog or the production catalog.")
