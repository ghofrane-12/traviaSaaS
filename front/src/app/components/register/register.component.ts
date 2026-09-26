import { Component, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators, AbstractControl } from '@angular/forms';
import { Router, ActivatedRoute, RouterModule } from '@angular/router';
import {
  Auth,
  createUserWithEmailAndPassword,
  signInWithPopup,
  GoogleAuthProvider,
  updateProfile,
  sendEmailVerification,
  UserCredential,
} from '@angular/fire/auth';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { UserService } from '../../services/user.service';
import { environment } from '../../environments/environment';

function passwordMatchValidator(control: AbstractControl) {
  const password = control.get('password');
  const confirm  = control.get('confirmPassword');
  if (!password || !confirm) return null;
  return password.value === confirm.value ? null : { mismatch: true };
}

@Component({
  selector: 'app-register',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterModule],
  templateUrl: './register.component.html',
  styleUrls: ['./register.component.scss'],
})
export class RegisterComponent implements OnInit {

  private fb       = inject(FormBuilder);
  private auth     = inject(Auth);
  private http     = inject(HttpClient);
  private router   = inject(Router);
  private route    = inject(ActivatedRoute);
  private userSvc  = inject(UserService);

  form: FormGroup = this.fb.group(
    {
      fullName:        ['', [Validators.required, Validators.minLength(2)]],
      email:           ['', [Validators.required, Validators.email]],
      password:        ['', [Validators.required, Validators.minLength(8)]],
      confirmPassword: ['', Validators.required],
      acceptTerms:     [false, Validators.requiredTrue],
    },
    { validators: passwordMatchValidator }
  );

  showPassword        = false;
  showConfirmPassword = false;
  loading             = false;
  googleLoading       = false;
  errorMessage        = '';
  private tenantId    = '';

  ngOnInit(): void {
    const tenantFromUrl = this.route.snapshot.queryParams['tenant'];
    if (tenantFromUrl) {
      sessionStorage.setItem('tenant_id', tenantFromUrl);
      this.tenantId = tenantFromUrl;
    } else {
      this.tenantId = this.userSvc.resolveTenantId();
    }
  }

  get f() { return this.form.controls; }

  get passwordMismatch(): boolean {
    return this.form.hasError('mismatch') && !!this.f['confirmPassword'].touched;
  }
private async sendVerificationEmail(cred: UserCredential): Promise<void> {
  const token = await cred.user.getIdToken();
  await firstValueFrom(
    this.http.post(
      `${environment.apiUrl}auth/send-verification`,
      {},
      {
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Tenant-ID': this.tenantId,
        },
      }
    )
  );
}
  async onSubmit(): Promise<void> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.loading      = true;
    this.errorMessage = '';

    const { fullName, email, password } = this.form.value;
    const parts     = (fullName as string).trim().split(' ');
    const firstName = parts[0];
    const lastName  = parts.slice(1).join(' ') || '';

    try {
      const cred: UserCredential = await createUserWithEmailAndPassword(
        this.auth, email, password
      );

      await updateProfile(cred.user, {
        displayName: `${firstName} ${lastName}`.trim(),
      });

      await this.createBackendSession(cred, firstName, lastName);
      await this.sendVerificationEmail(cred);
      this.router.navigate(['/verify-email']);

    } catch (err: any) {
      this.errorMessage = this.mapFirebaseError(err.code);
    } finally {
      this.loading = false;
    }
  }

  async onGoogleSignUp(): Promise<void> {
    this.googleLoading = true;
    this.errorMessage  = '';

    try {
      const provider = new GoogleAuthProvider();
      const cred     = await signInWithPopup(this.auth, provider);

      const displayName = cred.user.displayName || '';
      const parts       = displayName.split(' ');
      const firstName   = parts[0] || '';
      const lastName    = parts.slice(1).join(' ') || '';

      const session = await this.createBackendSession(cred, firstName, lastName);

      const redirect = sessionStorage.getItem('redirectAfterLogin') || '/dashboard';
      sessionStorage.removeItem('redirectAfterLogin');
      this.router.navigate([redirect]);

    } catch (err: any) {
      if (err.code !== 'auth/popup-closed-by-user') {
        this.errorMessage = this.mapFirebaseError(err.code);
      }
    } finally {
      this.googleLoading = false;
    }
  }

  private async createBackendSession(
    cred: UserCredential,
    firstName: string,
    lastName: string
  ): Promise<any> {
    const token = await cred.user.getIdToken();

    const session = await firstValueFrom(
      this.http.post<any>(
        `${environment.apiUrl}auth/session`,
        {},
        {
          headers: {
            Authorization:  `Bearer ${token}`,
            'X-Tenant-ID':  this.tenantId,
          },
        }
      )
    );

    this.userSvc.setUserDetails({
      user_id:      session.user_id,
      firebase_uid: session.firebase_uid,
      tenant_id:    session.tenant_id,
      role:         session.role,
      email:        session.email,
      first_name:   session.first_name || firstName,
      last_name:    session.last_name  || lastName,
      agency_name:  session.agency_name,
      agency_logo:  session.agency_logo,
      currency:     session.currency,
      tone:         session.tone,
    });

    return session;
  }

  private mapFirebaseError(code: string): string {
    const map: Record<string, string> = {
      'auth/email-already-in-use':    'Cette adresse email est déjà utilisée.',
      'auth/invalid-email':           'Adresse email invalide.',
      'auth/weak-password':           'Mot de passe trop faible (8 caractères minimum).',
      'auth/network-request-failed':  'Erreur réseau. Vérifiez votre connexion.',
      'auth/too-many-requests':       'Trop de tentatives. Réessayez plus tard.',
      'auth/popup-blocked':           'La popup a été bloquée. Autorisez les popups.',
      'auth/account-exists-with-different-credential':
                                      'Un compte existe déjà avec cet email.',
    };
    return map[code] ?? 'Une erreur est survenue. Veuillez réessayer.';
  }
}