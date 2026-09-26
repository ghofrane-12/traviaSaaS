// services/user.service.ts
import { Injectable, signal, computed } from '@angular/core';
import { Router } from '@angular/router';
import { environment } from '../environments/environment';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { Auth, signOut } from '@angular/fire/auth';

export interface UserSession {
  user_id: string;
  firebase_uid: string;
  tenant_id: string;
  role: 'super_admin' | 'admin' | 'sub_admin' | 'client' | 'visitor'; 
  email: string;
  first_name: string;
  last_name: string;
  agency_name: string;
  agency_logo?: string;
  currency?: string;
  tone?: string;
}

const SESSION_KEY = 'user_session';
const VISITOR_ID_KEY = 'visitor_id';
const VISITOR_SESSION_KEY = 'visitor_session_id';
const apiUrl = environment.apiUrl;
const TENANT_KEY = 'tenant_id';
const DEFAULT_TENANT_ID = 'a1b2c3d4-0001-0000-0000-000000000001';
@Injectable({ providedIn: 'root' })
export class UserService {

  private _session = signal<UserSession | null>(this.loadFromStorage());

  readonly session = this._session.asReadonly();
  readonly isLoggedIn = computed(() => !!this._session());
  readonly role = computed(() => this._session()?.role ?? null);
  readonly tenantId = computed(() => this.resolveTenantId());
  readonly isSuperAdmin = computed(() => this._session()?.role === 'super_admin');
  readonly isAdmin = computed(() => this._session()?.role === 'admin');
  readonly isUser = computed(() => this._session()?.role === 'client');
  readonly isSubAdmin = computed(() => this._session()?.role === 'sub_admin');
  readonly isVisitor = computed(() => this._session()?.role === 'visitor');
  readonly fullName = computed(() => {
    const s = this._session();
    return s ? `${s.first_name} ${s.last_name}`.trim() : 'Visiteur';
  });
  readonly agencyName = computed(() => this._session()?.agency_name ?? '');
  readonly agencyLogo = computed(() => this._session()?.agency_logo ?? null);
  readonly currency = computed(() => this._session()?.currency ?? 'EUR');
  readonly tone = computed(() => this._session()?.tone ?? 'formal');

  constructor(
    private router: Router,
    private auth: Auth,
    private http: HttpClient
  ) {}

  getVisitorId(): string {
    let visitorId = sessionStorage.getItem(VISITOR_ID_KEY);
    if (!visitorId) {
      visitorId = `visitor_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      sessionStorage.setItem(VISITOR_ID_KEY, visitorId);
    }
    return visitorId;
  }



resolveTenantId(): string {
  const session = this._session();
  if (session?.tenant_id) return session.tenant_id;

  const stored = sessionStorage.getItem(TENANT_KEY);
  if (stored) return stored;

  return DEFAULT_TENANT_ID;
}
  getVisitorSessionId(): string {
    let sessionId = sessionStorage.getItem(VISITOR_SESSION_KEY);
    if (!sessionId) {
      const today = new Date().toISOString().split('T')[0].replace(/-/g, '');
      sessionId = `${this.getVisitorId()}__${today}`;
      sessionStorage.setItem(VISITOR_SESSION_KEY, sessionId);
    }
    return sessionId;
  }

setUserDetails(session: UserSession): void {
  this._session.set(session);
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  sessionStorage.removeItem(VISITOR_ID_KEY);
  sessionStorage.removeItem(VISITOR_SESSION_KEY);
  sessionStorage.removeItem(TENANT_KEY); 
}
  patchUserDetails(partial: Partial<UserSession>): void {
    const current = this._session();
    if (!current) return;
    const updated = { ...current, ...partial };
    this._session.set(updated);
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(updated));
  }

  getUserDetails(): UserSession | null {
    return this._session();
  }

async getFreshIdToken(): Promise<string | null> {
  const firebaseUser = this.auth.currentUser;
  if (!firebaseUser) return null;
  

  return firebaseUser.getIdToken(false);
}

  async logout(): Promise<void> {
    await signOut(this.auth);
    this._session.set(null);
    sessionStorage.removeItem(SESSION_KEY);
    this.router.navigate(['/login']);
  }

  hasRole(...roles: UserSession['role'][]): boolean {
    const current = this._session();
    return !!current && roles.includes(current.role);
  }

  belongsToTenant(tenantId: string): boolean {
    return this._session()?.tenant_id === tenantId;
  }
async getFirebaseToken(): Promise<string | null> {
  return this.getFreshIdToken();  
}
  private loadFromStorage(): UserSession | null {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? (JSON.parse(raw) as UserSession) : null;
    } catch {
      return null;
    }
  }
}