from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ImageSettingsUpdate(BaseModel):
    dpi: int = Field(default=150, ge=72, le=600)
    image_format: str = Field(default="PNG", pattern="^(PNG|JPEG)$")
    jpeg_quality: int = Field(default=85, ge=1, le=100)
    grayscale: bool = False


class ImageSettingsRead(ImageSettingsUpdate):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
