from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()


class RegisterForm(forms.Form):
    email = forms.EmailField(label='Correo electrónico')
    password1 = forms.CharField(
        label='Contraseña',
        widget=forms.PasswordInput,
        min_length=12,
        help_text='Mínimo 12 caracteres',
    )
    invitation_code = forms.CharField(
        label='Código de invitación',
        required=False,
        max_length=13,
    )

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email=email, is_active=True).exists():
            raise ValidationError(
                'Este correo no está disponible. Prueba iniciando sesión o recuperando tu contraseña.'
            )
        return email

    def clean_password1(self):
        password = self.cleaned_data.get('password1')
        if password:
            validate_password(password)
        return password

    # Sin clean() de confirmación: el registro ya no pide repetir la contraseña.
    # En su lugar el formulario ofrece un botón para ver lo que se escribió, que
    # cumple la misma función de evitar errores de tecleo sin duplicar el campo.


class LoginForm(forms.Form):
    email = forms.EmailField(label='Correo electrónico')
    password = forms.CharField(label='Contraseña', widget=forms.PasswordInput)
