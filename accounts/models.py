from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from core.models import TimeStampedModel, UUIDModel


class UserRole(models.TextChoices):
    INVESTOR = "investor", "Investor"
    ANALYST = "analyst", "Analyst"
    FOUNDER = "founder", "Founder"
    STUDENT = "student", "Student"
    FINANCE_PRO = "finance_pro", "Finance Professional"
    OTHER = "other", "Other"


class UserManager(BaseUserManager):
    def create_user(self, telegram_id, password=None, **extra_fields):
        if not telegram_id:
            raise ValueError("telegram_id is required")
        user = self.model(telegram_id=telegram_id, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, telegram_id, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(telegram_id, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, UUIDModel, TimeStampedModel):
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    telegram_username = models.CharField(max_length=255, blank=True)
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=32, choices=UserRole.choices, blank=True)
    onboarding_completed = models.BooleanField(default=False)
    onboarding_step = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "telegram_id"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self):
        return self.telegram_username or str(self.telegram_id)


class GoogleService(models.TextChoices):
    GMAIL = "gmail", "Gmail"
    CALENDAR = "calendar", "Calendar"
    DRIVE = "drive", "Drive"
    SHEETS = "sheets", "Sheets"


class GoogleIntegration(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="google_integrations")
    service = models.CharField(max_length=32, choices=GoogleService.choices)
    access_token_encrypted = models.TextField(blank=True)
    refresh_token_encrypted = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "google_integrations"
        constraints = [
            models.UniqueConstraint(fields=["user", "service"], name="uniq_user_google_service"),
        ]

    def __str__(self):
        return f"{self.user_id} - {self.service}"
