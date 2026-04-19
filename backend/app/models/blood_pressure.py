from pydantic import BaseModel


class BloodPressureMeasurement(BaseModel):
    datetime: str
    systolic: int
    diastolic: int
    heart_rate: int
    measurement_type: str
    source_page: int
    source_column: str
    row_index_on_page: int
