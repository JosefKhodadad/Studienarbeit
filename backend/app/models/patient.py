from pydantic import BaseModel, EmailStr


class Patient(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    gender: str | None = None
    birth_date: str | None = None
    age_at_report_date: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
