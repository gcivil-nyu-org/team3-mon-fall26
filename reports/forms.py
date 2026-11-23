from django import forms

from .models import Report


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ('reason', 'details')
        labels = {'details': 'Details (optional)'}
        widgets = {'details': forms.Textarea(attrs={'rows': 3})}


class ResolveReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ('outcome',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'outcome' in self.fields:
            self.fields['outcome'].required = True
