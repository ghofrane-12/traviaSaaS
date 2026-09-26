# core/email_service.py
import httpx
from core.config import settings


def _send_via_brevo(to_email: str, subject: str, html: str, from_name: str) -> None:
    """
    Envoie un email via l'API HTTP Brevo.
    Fonctionne en local et en prod (pas de restriction IP).
    """
    payload = {
        "sender":   {"name": from_name, "email": settings.BREVO_SMTP_USER},
        "to":       [{"email": to_email}],
        "subject":  subject,
        "htmlContent": html,
    }

    response = httpx.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key":      settings.BREVO_API_KEY,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(f"Brevo API error {response.status_code}: {response.text}")


def send_verification_email(
    to_email:          str,
    display_name:      str,
    verification_link: str,
    agency_name:       str,
    agency_logo:       str | None,
    tenant_id:         str,
) -> None:
    """
    Envoie un email de vérification personnalisé par agence via Brevo SMTP.
    """
    first_name = display_name.split()[0] if display_name else "là"

    logo_html = (
        f'<img src="{agency_logo}" alt="{agency_name}" '
        f'style="height:48px;object-fit:contain;margin-bottom:24px;" />'
        if agency_logo
        else f'<h2 style="color:#e8834a;margin:0 0 24px;">{agency_name}</h2>'
    )

    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#f0f4f8;padding:40px 16px;">
    <tr><td align="center">
      <table width="100%"
             style="max-width:520px;background:#ffffff;border-radius:16px;
                    box-shadow:0 4px 24px rgba(0,0,0,0.08);overflow:hidden;">

        <!-- Header -->
        <tr>
          <td align="center" style="background:#e8834a;padding:32px 24px;">
            {logo_html}
            <p style="color:rgba(255,255,255,0.9);margin:0;font-size:15px;">
              Confirmez votre adresse email
            </p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:40px 36px;">
            <p style="font-size:16px;color:#1a1a2e;margin:0 0 12px;">
              Bonjour <strong>{first_name}</strong>,
            </p>
            <p style="font-size:15px;color:#4b5563;line-height:1.6;margin:0 0 28px;">
              Merci de vous être inscrit sur <strong>{agency_name}</strong> !<br>
              Cliquez sur le bouton ci-dessous pour confirmer votre adresse
              email et accéder à votre espace.
            </p>

            <!-- Bouton CTA -->
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td align="center" style="padding:8px 0 32px;">
                  <a href="{verification_link}"
                     style="background:#e8834a;color:#ffffff;text-decoration:none;
                            padding:14px 36px;border-radius:10px;font-size:16px;
                            font-weight:600;display:inline-block;
                            box-shadow:0 4px 14px rgba(232,131,74,0.4);">
                    Confirmer mon email
                  </a>
                </td>
              </tr>
            </table>

            <p style="font-size:13px;color:#9ca3af;line-height:1.6;margin:0 0 8px;">
              Ce lien expire dans <strong>24 heures</strong>.<br>
              Si vous n'avez pas créé de compte sur {agency_name},
              ignorez simplement cet email.
            </p>

            <!-- Lien texte fallback -->
            <p style="font-size:12px;color:#d1d5db;margin:16px 0 0;
                      word-break:break-all;">
              Ou copiez ce lien dans votre navigateur :<br>
              <a href="{verification_link}"
                 style="color:#e8834a;">{verification_link}</a>
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;padding:20px 36px;
                     border-top:1px solid #f0f0f0;">
            <p style="font-size:12px;color:#9ca3af;margin:0;text-align:center;">
              © {agency_name} — Cet email a été envoyé automatiquement,
              merci de ne pas y répondre.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    _send_via_brevo(
        to_email=to_email,
        subject=f"Confirmez votre email — {agency_name}",
        html=html,
        from_name=agency_name,
    )