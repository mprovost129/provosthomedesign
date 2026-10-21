"""Staff OTP secrets are encrypted; recovery codes are stored only as digests."""
import base64
import secrets
from datetime import timedelta
from urllib.parse import quote, urlencode
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.db import models
from django.utils import timezone
from django_otp.models import Device, ThrottlingMixin, TimestampMixin
from django_otp.oath import TOTP


def secret_cipher():
    def cipher(key):
        derived=HKDF(algorithm=hashes.SHA256(),length=32,salt=b'phd-staff-otp-v1',
            info=b'authenticator-secrets').derive(key.encode())
        return Fernet(base64.urlsafe_b64encode(derived))
    return MultiFernet([cipher(key) for key in [settings.SECRET_KEY,*settings.SECRET_KEY_FALLBACKS]])


class EncryptedOTPSecret(models.TextField):
    def from_db_value(self,value,expression,connection):
        if value is None:return value
        return secret_cipher().decrypt(value.encode()).decode()

    def get_prep_value(self,value):
        if value is None:return value
        return secret_cipher().encrypt(str(value).encode()).decode()


def new_device_key():
    return secrets.token_hex(20)


def enrollment_expiry():
    return timezone.now()+timedelta(minutes=15)


class StaffOTPDevice(TimestampMixin,ThrottlingMixin,Device):
    key=EncryptedOTPSecret(default=new_device_key,editable=False)
    last_t=models.BigIntegerField(default=-1)
    enrollment_expires_at=models.DateTimeField(default=enrollment_expiry)

    class Meta(Device.Meta):
        constraints=[models.UniqueConstraint(fields=['user'],name='wq_one_staff_otp_device')]

    def get_throttle_factor(self):
        return 1

    def verify_token(self,token):
        # Called only with a row lock, including enrollment confirmation.
        allowed,_=self.verify_is_allowed()
        if not allowed:return False
        valid=False
        if isinstance(token,str) and len(token)==6 and token.isascii() and token.isdigit():
            clock=TOTP(bytes.fromhex(self.key),step=30,digits=6)
            valid=clock.verify(int(token),tolerance=1,min_t=self.last_t+1)
            if valid:self.last_t=clock.t()
        if valid:
            self.throttle_reset(commit=False)
            self.set_last_used_timestamp(commit=False)
            self.save()
        else:self.throttle_increment()
        return valid

    @property
    def setup_key(self):
        return base64.b32encode(bytes.fromhex(self.key)).decode().rstrip('=')

    @property
    def config_url(self):
        issuer='Provost Home Design'
        return 'otpauth://totp/'+quote(issuer+':'+self.user.get_username(),safe='')+'?'+urlencode({
            'secret':self.setup_key,'issuer':issuer,'algorithm':'SHA1','digits':6,'period':30})


class StaffRecoveryCode(models.Model):
    device=models.ForeignKey(StaffOTPDevice,on_delete=models.CASCADE,related_name='recovery_codes')
    digest=models.CharField(max_length=64,unique=True)
    used_at=models.DateTimeField(null=True,blank=True)
    created_at=models.DateTimeField(default=timezone.now)
