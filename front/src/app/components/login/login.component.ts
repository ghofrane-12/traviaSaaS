// login.component.ts
import { Component, OnInit, inject } from '@angular/core';
import { Router, RouterModule, ActivatedRoute } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormControl, FormGroup, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Auth, signInWithEmailAndPassword, GoogleAuthProvider, FacebookAuthProvider, signInWithPopup, signInAnonymously, AuthError, getRedirectResult, linkWithCredential } from '@angular/fire/auth';
import { setPersistence, browserSessionPersistence } from 'firebase/auth';
import { UserService, UserSession } from '../../services/user.service';
import { environment } from '../../environments/environment';
import { firstValueFrom } from 'rxjs';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [RouterModule, CommonModule, ReactiveFormsModule, FormsModule],
  templateUrl: './login.component.html',
  styleUrls: ['./login.component.scss']
})
export class LoginComponent implements OnInit {

  returnUrl       = '/chat';
  isLoading       = false;
  isGoogleLoading = false;
  showPassword    = false;
  errorMessage    = '';
  isGuestLoading  = false;
  isFacebookLoading = false;
  private pendingFacebookCred: any = null;
  private pendingGoogleCred: any = null;
  currentTenantId = '';

  applyForm = new FormGroup({
    email:    new FormControl('', [Validators.required, Validators.email]),
    password: new FormControl('', [Validators.required, Validators.minLength(6)])
  });

  private apiUrl = environment.apiUrl;

  constructor(
    private router:      Router,
    private http:        HttpClient,
    private userService: UserService,
    private route:       ActivatedRoute,
    private auth:        Auth
  ) {}

async ngOnInit(): Promise<void> {
  this.currentTenantId = this.userService.resolveTenantId();

  this.route.queryParams.subscribe(params => {
    this.returnUrl = params['returnUrl'] || '/chat';
    if (params['tenant']) {
      sessionStorage.setItem('tenant_id', params['tenant']);
      this.currentTenantId = this.userService.resolveTenantId();
    }
  });

  try {
    const result = await getRedirectResult(this.auth);
    if (result) {
      const pendingToken = sessionStorage.getItem('pendingFacebookToken');
      if (pendingToken) {
        const facebookCred = FacebookAuthProvider.credential(pendingToken);
        await linkWithCredential(result.user, facebookCred);
        sessionStorage.removeItem('pendingFacebookToken');
      }
      const idToken = await result.user.getIdToken();
      await this.authenticateWithBackend(idToken);
    }
  } catch (error: any) {
    console.error('Redirect error:', error.code);
  }
}

  togglePassword(): void {
    this.showPassword = !this.showPassword;
  }

  async login(): Promise<void> {
    if (this.applyForm.invalid) {
      this.applyForm.markAllAsTouched();
      return;
    }

    this.isLoading    = true;
    this.errorMessage = '';
    const { email, password } = this.applyForm.value;

    try {
      await setPersistence(this.auth, browserSessionPersistence);
      const credential = await signInWithEmailAndPassword(this.auth, email!, password!);
      const idToken    = await credential.user.getIdToken();
      await this.authenticateWithBackend(idToken);
    } catch (error) {
      this.handleFirebaseError(error as AuthError);
    } finally {
      this.isLoading = false;
    }
  }

async loginWithGoogle(): Promise<void> {
  this.isGoogleLoading = true;
  this.errorMessage    = '';
  const provider = new GoogleAuthProvider();

  try {
    await setPersistence(this.auth, browserSessionPersistence);
    const result = await signInWithPopup(this.auth, provider);

    if (this.pendingFacebookCred) {
      try {
        await linkWithCredential(result.user, this.pendingFacebookCred);
        console.log('Facebook linked to Google ✅');
      } catch (linkError: any) {
        console.log('Already linked or error:', linkError.code);
      }
      this.pendingFacebookCred = null;
    }

    const idToken = await result.user.getIdToken();
    await this.authenticateWithBackend(idToken);

  } catch (error) {
    this.handleFirebaseError(error as AuthError);
  } finally {
    this.isGoogleLoading = false;
  }
}

async loginWithFacebook(): Promise<void> {
  console.log('Facebook login started');
  this.isFacebookLoading = true;
  this.errorMessage      = '';
  const provider = new FacebookAuthProvider();

  try {
    await setPersistence(this.auth, browserSessionPersistence);
    const result  = await signInWithPopup(this.auth, provider);
    console.log('Facebook user uid:', result.user.uid);
console.log('Facebook provider:', result.user.providerData);

    if (this.pendingGoogleCred) {
      await linkWithCredential(result.user, this.pendingGoogleCred);
      this.pendingGoogleCred = null;
    }

    const idToken = await result.user.getIdToken();
    console.log('idToken length:', idToken.length);
console.log('idToken preview:', idToken.substring(0, 50));
    await this.authenticateWithBackend(idToken);

  } catch (error: any) {
    console.log('Facebook error code:', error.code);

    if (error.code === 'auth/account-exists-with-different-credential') {
      this.pendingFacebookCred = FacebookAuthProvider.credentialFromError(error);
      this.errorMessage = '⚠️ Cet email est déjà utilisé avec Google. Cliquez sur "Google" ci-dessous pour vous connecter et lier les comptes automatiquement.';
    } else {
      this.handleFirebaseError(error as AuthError);
    }
  } finally {
    this.isFacebookLoading = false;
  }
}
async continueAsGuest(): Promise<void> {
  this.isGuestLoading = true;
  this.errorMessage   = '';

  try {
    await setPersistence(this.auth, browserSessionPersistence);
    const credential = await signInAnonymously(this.auth);
    const tenantId   = this.userService.resolveTenantId();

    const tenantInfo = await firstValueFrom(
      this.http.get<any>(`${this.apiUrl}auth/tenant/${tenantId}/public`)
    );

    const visitorSession: UserSession = {
      user_id:      credential.user.uid,
      firebase_uid: credential.user.uid,
      tenant_id:    tenantId,
      role:         'visitor',
      email:        null as any,
      first_name:   'Visiteur',
      last_name:    '',
      agency_name:  tenantInfo.agency_name,
      agency_logo:  tenantInfo.agency_logo,
      currency:     tenantInfo.currency,
      tone:         tenantInfo.tone,
    };

    this.userService.setUserDetails(visitorSession);

    await new Promise(resolve => setTimeout(resolve, 50));

    this.router.navigate(['/chat']);

  } catch (error) {
    console.error('Guest login error:', error);
    this.errorMessage = "Erreur de connexion en tant qu'invité.";
  } finally {
    this.isGuestLoading = false;
  }
}

  private async authenticateWithBackend(idToken: string): Promise<void> {
    return new Promise((resolve, reject) => {
      this.http.post<UserSession>(
        `${this.apiUrl}auth/session`,
        {},
        {
          headers: {
            Authorization:  `Bearer ${idToken}`,
            'X-Tenant-ID':  this.currentTenantId,  
          }
        }
      ).subscribe({
        next: (session) => {
          this.userService.setUserDetails(session);
          sessionStorage.removeItem('redirectAfterLogin');
          this.redirectByRole(session.role);
          resolve();
        },
        error: (err) => {
          if (err.status === 403) {
            this.errorMessage = "Votre compte n'est pas autorisé sur cette agence.";
          } else if (err.status === 404) {
            this.errorMessage = "Aucun compte trouvé.";
          } else {
            this.errorMessage = "Erreur de connexion.";
          }
          reject(err);
        }
      });
    });
  }

private handleFirebaseError(error: AuthError): void {
  const messages: Record<string, string> = {
    'auth/user-not-found':     'Aucun compte avec cet email.',
    'auth/wrong-password':     'Mot de passe incorrect.',
    'auth/too-many-requests':  'Trop de tentatives. Réessayez plus tard.',
    'auth/invalid-credential': 'Email ou mot de passe invalide.',
    'auth/popup-closed-by-user': 'Connexion annulée.',
    'auth/account-exists-with-different-credential': 
      'Cet email est déjà utilisé avec un autre provider. Connectez-vous avec Google.',
  };
  this.errorMessage = messages[error.code] ?? `Erreur : ${error.message}`;
}

  private redirectByRole(role: string): void {
    if (role === 'super_admin') {
      this.router.navigate(['/super-admin']);
    } else {
      this.router.navigate(['/chat']);
    }
  }
}