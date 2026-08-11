#!/usr/bin/env bash
# Локальный запуск: Django и бот из одного репозитория, в фоне, с логами.
#
#   ./dev.sh start          поднять всё (сначала накатит миграции)
#   ./dev.sh stop           погасить
#   ./dev.sh restart        перезапустить всё
#   ./dev.sh restart bot    перезапустить только бота
#   ./dev.sh logs bot       смотреть лог живьём (Ctrl+C — выйти, бот работает дальше)
#   ./dev.sh status         кто сейчас жив и в какой базе
#   ./dev.sh manage …       любая команда manage.py в том же окружении
#
# Правки в текстах: Django подхватывает их сам, боту нужен `./dev.sh restart bot`
# — у aiogram нет автоперезагрузки.
#
# Процессы ищутся по командной строке, а не по pid-файлам: так скрипт видит и
# то, что было запущено руками до него. Иначе `start` поднял бы второго бота, а
# два поллера на одном токене отбирают апдейты друг у друга.

set -euo pipefail
cd "$(dirname "$0")"

LOG_DIR=.dev
mkdir -p "$LOG_DIR"

PY="$(poetry env info -p 2>/dev/null)/bin/python"
[ -x "$PY" ] || { echo "не нашёл окружение poetry — сделай 'poetry install'"; exit 1; }

pattern_for() {
	case "$1" in
		bot)    echo "manage.py runbot" ;;
		web)    echo "manage.py runserver" ;;
		celery) echo "celery -A cybernexvpn worker" ;;
		beat)   echo "celery -A cybernexvpn beat" ;;
		*)      echo "не знаю такого: $1 (есть bot, web, celery, beat)" >&2; exit 1 ;;
	esac
}

pids_of() { pgrep -f "$(pattern_for "$1")" 2>/dev/null || true; }

start_one() {
	local name="$1"; shift
	local running
	running="$(pids_of "$name")"
	if [ -n "$running" ]; then
		echo "  $name уже работает (pid $(echo "$running" | tr '\n' ' '))"
		return
	fi
	nohup "$@" >>"$LOG_DIR/$name.log" 2>&1 &
	sleep 2
	if [ -n "$(pids_of "$name")" ]; then
		echo "  $name запущен"
	else
		echo "  $name упал сразу:"
		tail -n 15 "$LOG_DIR/$name.log"
		return 1
	fi
}

stop_one() {
	local name="$1" pids
	pids="$(pids_of "$name")"
	if [ -z "$pids" ]; then
		echo "  $name не запущен"
		return
	fi
	# shellcheck disable=SC2086
	kill $pids 2>/dev/null || true
	# Боту нужно время закрыть сессию к Telegram, иначе следующий запуск
	# упирается в «terminated by other getUpdates request».
	for _ in $(seq 1 24); do
		[ -z "$(pids_of "$name")" ] && break
		sleep 0.25
	done
	pids="$(pids_of "$name")"
	# shellcheck disable=SC2086
	[ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
	echo "  $name остановлен"
}

CELERY="$(dirname "$PY")/celery"

run_one() {
	case "$1" in
		bot)    start_one bot "$PY" manage.py runbot ;;
		web)    start_one web "$PY" manage.py runserver 127.0.0.1:8000 ;;
		celery) start_one celery "$CELERY" -A cybernexvpn worker -l info ;;
		beat)   start_one beat "$CELERY" -A cybernexvpn beat -l info ;;
	esac
}

ALL="web bot celery beat"

current_db() {
	sed -n 's#^DATABASE_URL=.*/\([^/?]*\)$#\1#p' .env | head -1
}

cmd_start() {
	if ! nc -z -G 2 localhost 5434 >/dev/null 2>&1; then
		echo "  ⚠️  Postgres на 5434 не отвечает — docker start cybernexvpn-postgres"
		exit 1
	fi
	echo "База: $(current_db)"
	echo "Миграции:"
	"$PY" manage.py migrate --noinput | tail -n 3
	echo "Запуск:"
	for name in $ALL; do run_one "$name"; done
	echo
	echo "Админка: http://127.0.0.1:8000/admin/"
	echo "Логи:    ./dev.sh logs bot"
}

case "${1:-start}" in
	start) cmd_start ;;
	stop)
		echo "Остановка:"
		for name in $ALL; do stop_one "$name"; done
		;;
	status)
		echo "База: $(current_db)"
		for name in $ALL; do
			pids="$(pids_of "$name")"
			if [ -n "$pids" ]; then
				echo "  $name: работает (pid $(echo "$pids" | tr '\n' ' '))"
			else
				echo "  $name: остановлен"
			fi
		done
		;;
	restart)
		shift || true
		targets="${*:-$ALL}"
		for name in $targets; do stop_one "$name"; done
		for name in $targets; do run_one "$name"; done
		;;
	logs)
		name="${2:-bot}"
		touch "$LOG_DIR/$name.log"
		tail -n 40 -f "$LOG_DIR/$name.log"
		;;
	manage)
		shift
		"$PY" manage.py "$@"
		;;
	*)
		sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
		exit 1
		;;
esac
