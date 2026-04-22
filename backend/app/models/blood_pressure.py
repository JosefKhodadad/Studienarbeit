from pydantic import BaseModel, Field


ENTRY_STATUS_ORIGINAL = "original"
ENTRY_STATUS_MANUAL = "manual"
ENTRY_STATUS_EDITED = "edited"
ENTRY_STATUS_RESTORED = "restored_original"

VALID_ENTRY_STATUSES = (
    ENTRY_STATUS_ORIGINAL,
    ENTRY_STATUS_MANUAL,
    ENTRY_STATUS_EDITED,
    ENTRY_STATUS_RESTORED,
)


class OriginalMeasurementValues(BaseModel):
    """Snapshot of the values a measurement had right after the import.

    Only populated for rows that originated in a PDF import. Used to decide
    whether an edit restores the original state (``restored_original``) or
    diverges from it (``edited``).
    """

    datetime: str
    systolic: int
    diastolic: int
    heart_rate: int
    measurement_type: str


class BloodPressureMeasurement(BaseModel):
    entry_id: str | None = None
    datetime: str
    systolic: int
    diastolic: int
    heart_rate: int
    measurement_type: str
    source_page: int = 0
    source_column: str = "import"
    row_index_on_page: int = 0
    source: str = "import"
    source_file: str | None = None
    import_batch_id: str | None = None
    entry_status: str = ENTRY_STATUS_ORIGINAL
    original_values: OriginalMeasurementValues | None = None
    edited_at: str | None = None

    model_config = {"populate_by_name": True}
