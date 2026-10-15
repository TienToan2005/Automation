from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer

# DB lưu UTC không kèm múi giờ; khi trả JSON gắn "Z" để trình duyệt hiểu đúng
UTCDateTime = Annotated[datetime, PlainSerializer(lambda d: d.isoformat() + "Z", return_type=str, when_used="json")]
