"""Agent 可见的铁路购票能力定义（schema 与 Java REST 一一对应）。

执行路径只有一条：Python 收到工具调用 -> REST 调 Java。
与 backend-java 铁路/用户接口保持一致；参数用站名（Java 侧模糊匹配），降低大模型臆造 id 的风险。
覆盖范围：业务操作 + 只读查询（车站/列车/车次/联系人/资料/订单详情）；不含站点列车车次的管理写操作与注册。
"""


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": props, "required": required}},
    }


def query_tickets_schema() -> dict:
    return _fn(
        "query_tickets",
        "按出发地、目的地、日期查询火车票（可指定席别）。返回车次、时间、票价与余票。",
        {
            "from": {"type": "string", "description": "出发站名，如 北京南"},
            "to": {"type": "string", "description": "到达站名，如 上海虹桥"},
            "date": {"type": "string", "description": "乘车日期 YYYY-MM-DD"},
            "seatClass": {"type": "string", "description": "可选席别：二等座/一等座/商务座"},
        },
        ["from", "to", "date"],
    )


def buy_ticket_schema() -> dict:
    return _fn(
        "buy_ticket",
        "为当前用户购买指定车次的一张票（购票前应先查票确认车次与余票）。默认成人票；儿童请填 ticketType=CHILD，并会先核对儿童票规则。",
        {
            "tripId": {"type": "integer", "description": "车次记录 id（来自 query_tickets）"},
            "seatClass": {"type": "string", "description": "席别：二等座/一等座/商务座"},
            "from": {"type": "string", "description": "出发站名"},
            "to": {"type": "string", "description": "到达站名"},
            "ticketType": {"type": "string", "description": "票种：ADULT(默认)/CHILD/STUDENT", "enum": ["ADULT", "CHILD", "STUDENT"]},
        },
        ["tripId", "seatClass", "from", "to"],
    )


def my_orders_schema() -> dict:
    return _fn("my_orders", "查询当前用户的全部订单（含状态，用于续聊/退票时确认要操作哪张）。", {}, [])


def order_detail_schema() -> dict:
    return _fn(
        "order_detail",
        "按 requestId 查询某一笔订单的详情。",
        {"requestId": {"type": "string", "description": "订单 requestId（来自 my_orders）"}},
        ["requestId"],
    )


def pay_ticket_schema() -> dict:
    return _fn(
        "pay_ticket",
        "对指定 requestId 的待支付订单完成支付。",
        {"requestId": {"type": "string", "description": "订单 requestId"}},
        ["requestId"],
    )


def cancel_ticket_schema() -> dict:
    return _fn(
        "cancel_ticket",
        "对指定 requestId 的订单退票（已支付/待支付均可，退票会释放余票）。退票前可先调 my_orders 确认是哪张。",
        {"requestId": {"type": "string", "description": "订单 requestId"}},
        ["requestId"],
    )


def list_stations_schema() -> dict:
    return _fn(
        "list_stations",
        "查询车站（可用站名关键字过滤）。需要确认站名是否存在时使用。",
        {"kw": {"type": "string", "description": "可选：站名关键字，如 北京"}},
        [],
    )


def list_trains_schema() -> dict:
    return _fn("list_trains", "查询系统现有列车（车次号/车型/编组）。", {}, [])


def list_trips_schema() -> dict:
    return _fn(
        "list_trips",
        "查询开行车次（可按日期或 trainId 过滤）。",
        {
            "date": {"type": "string", "description": "可选：日期 YYYY-MM-DD"},
            "trainId": {"type": "integer", "description": "可选：列车 id"},
        },
        [],
    )


def list_contacts_schema() -> dict:
    return _fn("list_contacts", "查询当前用户的常用联系人（乘车人）。", {}, [])


def add_contact_schema() -> dict:
    return _fn(
        "add_contact",
        "新增一个常用联系人（乘车人）。",
        {
            "name": {"type": "string", "description": "姓名，必填"},
            "idType": {"type": "string", "description": "证件类型，默认身份证"},
            "idNo": {"type": "string", "description": "证件号"},
            "phone": {"type": "string", "description": "手机号"},
            "age": {"type": "integer", "description": "年龄"},
        },
        ["name"],
    )


def get_profile_schema() -> dict:
    return _fn("get_profile", "查询当前用户个人资料（姓名/部门/证件/是否学生等）。", {}, [])


def all_schemas() -> list[dict]:
    return [
        query_tickets_schema(), buy_ticket_schema(),
        my_orders_schema(), order_detail_schema(),
        pay_ticket_schema(), cancel_ticket_schema(),
        list_stations_schema(), list_trains_schema(), list_trips_schema(),
        list_contacts_schema(), add_contact_schema(), get_profile_schema(),
    ]
