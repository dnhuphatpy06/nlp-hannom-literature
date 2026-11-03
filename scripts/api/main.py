import json
import sys

import sinonom_service

IMAGE = "img/test_api/test_1.png"
TEXT = "皇帝制曰朕惟人臣事上百勞靡此于壯懷"


if __name__ == "__main__":
    # sinonom_service.upload(IMAGE)  # upload (nếu cần) rồi OCR
    data = sinonom_service.ocr(IMAGE)
    print(data["result_ocr_text"])

    data = sinonom_service.transliterate(TEXT)
    print(data["result_hannom_text"])
    print(data["result_text_transcription"])
