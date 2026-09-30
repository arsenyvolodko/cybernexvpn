"""Как зеркала отдают ссылку подписки — по замерам из сетей самих людей.

Свои серверы тут бесполезны: они в дата-центрах, где не душат. Замер делает
страница-мостик при подключении и присылает нам, см. `api/telemetry/probe.py`.

Главное число здесь — не доля успехов по каждому адресу, а **расхождение**:
сколько людей, у которых один адрес работает, а другой нет. Именно оно решает,
нужно ли выдавать разным людям разные адреса. Если расхождений нет, вся затея с
выбором домена лишняя, сколько бы отказов ни было в сумме.
"""

from __future__ import annotations

from collections import defaultdict

from nexvpn.models import SubHostProbe

TITLES = {
    "sub-nex.com": "sub-nex.com (через РФ-зеркало)",
    "sub.cybernexapp.com": "sub.cybernexapp.com (напрямую, OVH)",
}

OK = SubHostProbe.Outcome.OK
SLOW = SubHostProbe.Outcome.SLOW
FAILED = SubHostProbe.Outcome.FAILED


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) // 2


def _who(row) -> str | None:
    """Чей это замер. Человек известен не всегда: мостик открывают и по ссылке."""
    if row["user_id"]:
        return f"u{row['user_id']}"
    if row["short_uuid"]:
        return f"s{row['short_uuid']}"
    return None


def host_health(date_from, date_to) -> dict:
    rows = list(
        SubHostProbe.objects
        .filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
        .values("host", "outcome", "ms", "user_id", "short_uuid", "created_at")
        .order_by("created_at")
    )

    counts: dict[str, dict[str, int]] = defaultdict(lambda: {OK: 0, SLOW: 0, FAILED: 0})
    timings: dict[str, list[int]] = defaultdict(list)
    people: dict[str, set[str]] = defaultdict(set)
    # Последний исход по каждому человеку и адресу: важно не «сколько раз
    # отказало», а работает ли адрес у него сейчас. Строки уже по возрастанию
    # времени, поэтому последняя запись затирает предыдущие.
    latest: dict[str, dict[str, str]] = defaultdict(dict)

    for row in rows:
        host, outcome = row["host"], row["outcome"]
        counts[host][outcome] += 1
        if outcome != FAILED:
            timings[host].append(row["ms"])
        who = _who(row)
        if who:
            people[host].add(who)
            latest[who][host] = outcome

    hosts = []
    for host in sorted(counts, key=lambda h: -sum(counts[h].values())):
        c = counts[host]
        total = c[OK] + c[SLOW] + c[FAILED]
        hosts.append({
            "host": host,
            "title": TITLES.get(host, host),
            "ok": c[OK],
            "slow": c[SLOW],
            "failed": c[FAILED],
            "total": total,
            # «Удалось» — это ok и slow вместе: ссылка доехала, пусть и не быстро.
            "share_ok": round(100 * (c[OK] + c[SLOW]) / total) if total else None,
            "median_ms": _median(timings[host]),
            "users": len(people[host]),
        })

    return {"hosts": hosts, "split": _split(latest), "probes": len(rows)}


def _split(latest: dict[str, dict[str, str]]) -> dict:
    """Расхождения между адресами в пределах одного человека.

    Считаем только тех, у кого проверены оба адреса: иначе «работает только
    один» нельзя отличить от «второй не проверяли».
    """
    both_ok = only = none = 0
    only_by_host: dict[str, int] = defaultdict(int)

    for hosts in latest.values():
        if len(hosts) < 2:
            continue
        working = [h for h, outcome in hosts.items() if outcome != FAILED]
        if len(working) == len(hosts):
            both_ok += 1
        elif working:
            only += 1
            for h in working:
                only_by_host[h] += 1
        else:
            none += 1

    return {
        "both_ok": both_ok,
        "only_one": only,
        "none": none,
        "checked": both_ok + only + none,
        "only_by_host": [
            {"host": h, "title": TITLES.get(h, h), "users": n}
            for h, n in sorted(only_by_host.items(), key=lambda kv: -kv[1])
        ],
    }
