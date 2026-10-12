"""오타 후보에서 제외할 '정상 단어' 목록 (소문자, 4자 이상).
변경분에서 한 번만 나온 단어가 다른 단어와 철자가 비슷해도, 이 목록에 있는 단어면 오타로 보지 않는다.
(예: font-weight 의 weight 와 height)  팀에서 자주 나오는 오탐 단어를 여기에 추가하세요.
dify/nodes/parse_diff.py 의 KNOWN_WORDS 와 내용이 같아야 한다 (테스트가 확인)."""

KNOWN_WORDS = frozenset("""
weight height width left right top bottom margin padding border color background display position flex grid
align justify content center items font size style family line letter spacing opacity overflow visible hidden
scroll cursor pointer transform transition animation shadow radius solid dashed dotted block inline static
fixed absolute relative sticky wrap nowrap column reverse stretch baseline bold bolder lighter normal italic
underline decoration uppercase lowercase capitalize repeat cover contain image gradient filter blur clip auto
none inherit initial unset calc translate rotate scale skew origin
string number boolean object array first last next prev previous parent child children item count total price
amount balance account name names type types label title text message error errors event handler method class
function return public private final const where select insert update delete limit offset order group having
table field fields query param params header footer index value values status state target source result
results request response length width depth level range start end begin finish close open read write load save
send receive user users group groups role roles token tokens token key keys code codes date time dates times
month year week hour minute second email phone address mobile desktop tablet small medium large short long
light dark black white green yellow orange purple gray grey
""".split())
