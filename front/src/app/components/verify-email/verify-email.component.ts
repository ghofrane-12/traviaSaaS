// verify-email.component.ts
import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { Auth, reload, signOut } from '@angular/fire/auth';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
@Component({
  selector: 'app-verify-email',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './verify-email.component.html',
  styleUrls: ['./verify-email.component.scss'],
})
export class VerifyEmailComponent implements OnInit, OnDestroy {

  private authService = inject(Auth);
  private router      = inject(Router);
  private http = inject(HttpClient);


  userEmail    = this.authService.currentUser?.email ?? '';
  verified     = false;
  resendDisabled = false;
  resendLabel  = 'Renvoyer l\'email';
  errorMsg     = '';

  private pollInterval: any;
  private cooldownInterval: any;


ngOnInit(): void {
  const user = this.authService.currentUser;
  if (user?.isAnonymous) {
    this.router.navigate(['/chat']);
    return;
  }

  this.checkVerification();
  
  this.pollInterval = setInterval(() => this.checkVerification(), 4000);
}
  ngOnDestroy(): void {
    clearInterval(this.pollInterval);
    clearInterval(this.cooldownInterval);
  }
async checkVerification(): Promise<void> {
  const user = this.authService.currentUser;
  if (!user || this.verified) return;

  try {
    await reload(user);
    if (user.emailVerified) {
      this.verified = true;
      clearInterval(this.pollInterval);
      setTimeout(() => {
        const redirect = sessionStorage.getItem('redirectAfterLogin') || '/chat';
        sessionStorage.removeItem('redirectAfterLogin');
        this.router.navigate([redirect]);
      }, 1500);
    }
  } catch {
  }
}

async resendEmail(): Promise<void> {
  const user = this.authService.currentUser;
  if (!user) return;

  this.errorMsg = '';
  try {
    const token = await user.getIdToken();
    const tenantId = sessionStorage.getItem('tenant_id') ?? '';

    const { HttpClient } = await import('@angular/common/http');
    await firstValueFrom(
      this.http.post(
        `${environment.apiUrl}auth/send-verification`,
        {},
        {
          headers: {
            Authorization: `Bearer ${token}`,
            'X-Tenant-ID': tenantId,
          },
        }
      )
    );
    this.startResendCooldown(60);
  } catch (err: any) {
    if (err.status === 429) {
      this.errorMsg = 'Trop de tentatives. Attendez quelques minutes.';
    } else {
      this.errorMsg = 'Erreur lors du renvoi. Réessayez.';
    }
  }
}

  private startResendCooldown(seconds: number): void {
    this.resendDisabled = true;
    let remaining = seconds;
    this.resendLabel = `Renvoyer dans ${remaining}s`;

    this.cooldownInterval = setInterval(() => {
      remaining--;
      this.resendLabel = `Renvoyer dans ${remaining}s`;
      if (remaining <= 0) {
        clearInterval(this.cooldownInterval);
        this.resendDisabled = false;
        this.resendLabel = "Renvoyer l'email";
      }
    }, 1000);
  }

  async onLogout(): Promise<void> {
    await signOut(this.authService);
    this.router.navigate(['/login']);
  }
}