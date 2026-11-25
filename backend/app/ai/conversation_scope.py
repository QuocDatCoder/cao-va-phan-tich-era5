"""Server-owned conversational boundaries; model prose cannot override refusals."""
import re
from backend.app.ai.project_knowledge import normalize

REFUSAL = 'ARE không hỗ trợ nội dung ngoài phạm vi dự án. Bạn có thể hỏi về dữ liệu ERA5, phân tích khí tượng hoặc cách sử dụng ứng dụng.'
GREETING = 'Chào bạn! ARE sẵn sàng hỗ trợ dữ liệu ERA5 và cách sử dụng ứng dụng.'
SOCIAL = {
    'chao', 'chao ban', 'chao are', 'xin chao', 'hello', 'hi', 'hey',
    'cam on', 'cam on ban', 'cam on are', 'thanks', 'thank you',
    'ban khoe khong', 'may khoe khong', 'khoe khong', 'tam biet', 'bye',
    'ok', 'oke', 'duoc roi',
}
DOMAIN = re.compile(r'\b(era5|khi tuong|khi hau|nhiet do|luong mua|diem suong|toc do gio|huong gio|buc xa|kelvin|celsius|utc|do phu|du lieu|bieu do|thong ke|phan tich|cds|copernicus|dataset|collection|snapshot|parser|quality|backend|frontend|streamlit|fastapi|sqlite|du an|do an|ung dung|code du an|temperature|precipitation|wind|radiation|climate|weather|coverage)\b')
OUTSIDE = re.compile(r'\b(com tam|an gi|mon an|nau an|xem phim|hoc tieng anh|choi game|ca cuoc|danh bac|movie|netflix|learn english|viet bai van)\b')


def social_answer(question):
    text=normalize(question)
    if any(word in text for word in ('cam on', 'thanks', 'thank you')):
        return 'Rất vui được hỗ trợ bạn.'
    if any(word in text for word in ('tam biet', 'bye')):
        return 'Chào bạn, hẹn gặp lại!'
    if text.strip(' .!?') in {'ok', 'oke', 'duoc roi'}:
        return 'Được nhé. Khi cần, bạn có thể hỏi ARE về dữ liệu ERA5 hoặc cách sử dụng ứng dụng.'
    return GREETING


def scope(question, history=()):
    text = normalize(question).strip(' .!?;,:')
    if text in SOCIAL:
        return 'social'
    # An unrelated request stays unrelated even if it mentions ERA5 as a pretext.
    if OUTSIDE.search(text):
        return 'unsupported'
    if DOMAIN.search(text) or re.search(r'\bare\b', text) or any(x in question.lower() for x in ['mưa','gió','khí tượng']):
        return 'domain'
    if re.fullmatch(r'(ban )?(lam duoc gi|giup duoc gi|ho tro gi|co the lam gi)', text):
        return 'domain'
    if (re.search(r'\b(so lieu|bo nay|bo da chon|trong kho|trung binh|tu ngay|den ngay)\b', text)
            or 'vẽ' in question.lower() or text == 'co gi'):
        return 'domain'
    if re.fullmatch(r'(cho vi du( nhe)?|giai thich them|noi ro hon|tai sao|vi sao|tiep tuc|cai nay la gi)', text):
        previous = next((m.content for m in reversed(history) if m.role == 'user'), '')
        if DOMAIN.search(normalize(previous)) and not OUTSIDE.search(normalize(previous)):
            return 'domain'
    previous = next((m.content for m in reversed(history) if m.role == 'user'), '')
    if DOMAIN.search(normalize(previous)) and not OUTSIDE.search(normalize(previous)):
        if (re.search(r'\d{1,4}[-/]\d{1,2}|\d{1,2}:\d{2}', text)
                or re.fullmatch(r'(theo )?(gio|ngay|thang|nam|hourly|daily|monthly|yearly)', text)
                or re.fullmatch(r'-?\d+(\.\d+)?\s*[,; ]\s*-?\d+(\.\d+)?', text)):
            return 'domain'
        prompt=next((normalize(m.content) for m in reversed(history) if m.role=='assistant'), '')
        if ('dia diem' in prompt and len(text.split())<=8 and len(text)<=100
                and not re.search(r'\b(muon|hay|hoc|viet|ke|phim|game|an|uong|ignore)\b', text)):
            return 'domain'
    # Lack of a keyword is not proof that a question is unrelated. The Agent
    # must classify this branch with context before exposing any regular tools.
    return 'ambiguous'
