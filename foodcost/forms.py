from django import forms
from .models import Product


class ProductWithPriceForm(forms.Form):
    name = forms.CharField(label="Название продукта", max_length=255)

    unit = forms.ChoiceField(
        label="Единица измерения",
        choices=[
            ("kg", "кг"),
            ("g", "г"),
            ("l", "л"),
            ("ml", "мл"),
            ("pcs", "шт"),
        ],
    )

    price = forms.DecimalField(label="Цена закупки", max_digits=10, decimal_places=2)

    date = forms.DateField(
        label="Дата цены",
        widget=forms.DateInput(attrs={"type": "date"})
    )


class ProductPriceUpdateForm(forms.Form):
    product = forms.ModelChoiceField(
        label="Продукт",
        queryset=Product.objects.all()
    )

    price = forms.DecimalField(
        label="Новая цена закупки",
        max_digits=10,
        decimal_places=2
    )

    date = forms.DateField(
        label="Дата новой цены",
        widget=forms.DateInput(attrs={"type": "date"})
    )