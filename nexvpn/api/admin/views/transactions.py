import tempfile

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework.decorators import api_view

from nexvpn.models import Transaction, NexUser


@api_view(["GET"])
def get_transactions_history(request, user_id: int) -> FileResponse:
    user = get_object_or_404(NexUser, pk=user_id)
    res = f"Данные актуальны на момент {now().strftime("%d.%m.%Y %H:%M:%S")}.\n\n"
    res += f"Текущий баланс: {user.balance}₽\n\n"
    res += "История транзакций:\n"
    transactions = Transaction.objects.filter(user_id=user_id).order_by("-created_at")
    res += "\n".join(map(str, transactions))

    with tempfile.NamedTemporaryFile(mode="w+", delete=True) as temp_file:
        temp_file.write(res)
        temp_file.flush()
        response = FileResponse(open(temp_file.name, "rb"), as_attachment=True, filename=f"transactions.txt")
        return response
