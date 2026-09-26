// services/super-admin.service.ts
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, from, switchMap } from 'rxjs';
import { Auth } from '@angular/fire/auth';
import { environment } from '../environments/environment';


export interface TenantStats {
  tenant_id:    string;
  name:         string;
  logo_url?:    string;
  currency:     string;
  tone:         string;
  admin_count:  number;
  client_count: number;
  total_users:  number;
  facebook_page_token?: string;
  created_at?:  string;
}

export interface GlobalStats {
  total_tenants:    number;
  total_users:      number;
  total_admins:     number;
  total_clients:    number;
  total_sub_admins: number;
  new_users_7d:     number;
  new_users_30d:    number;
  total_bookings:   number;   
  total_tickets:    number;   
  avg_rating:       number; 
  tenants:          TenantStats[];
}

export interface TenantPayload {
  name:               string;
  logo_url?:          string;
  currency:           string;
  margin_percentage:  number;
  tone:               string;
  facebook_page_id?:  string;  // ← AJOUTEZ
  facebook_page_token: string;

}

export interface TenantResponse extends TenantPayload {
  tenant_id:        string;
  facebook_page_id?: string;  // ← AJOUTEZ
  created_at:       string;
  updated_at:       string;
}

export interface AdminPayload {
  email:            string;
  password:         string;
  first_name:       string;
  last_name:        string;
  can_create_admin: boolean;
}

export interface UserItem {
  user_id:          string;
  firebase_uid:     string;
  tenant_id:        string;
  email:            string;
  first_name:       string;
  last_name:        string;
  role:             string;
  can_create_admin: boolean;
  is_active:        boolean;
  created_at:       string;
}

// ─── Service ──────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class SuperAdminService {

  private readonly BASE: string;
  private auth = inject(Auth);

  constructor(private http: HttpClient) {
    // env.apiUrl peut être 'http://localhost:8000/api/' ou 'http://localhost:8000/api'
    this.BASE = environment.apiUrl.replace(/\/$/, '') + '/superadmin';
  }

  private headers$(): Observable<HttpHeaders> {
    return from(this.auth.currentUser!.getIdToken()).pipe(
      switchMap(token => [new HttpHeaders({ Authorization: `Bearer ${token}` })])
    );
  }

  // ── Stats ──────────────────────────────────────────────────────────────────
  getStats(): Observable<GlobalStats> {
    return this.headers$().pipe(
      switchMap(h => this.http.get<GlobalStats>(`${this.BASE}/stats`, { headers: h }))
    );
  }

  // ── Tenants ────────────────────────────────────────────────────────────────
  listTenants(): Observable<TenantResponse[]> {
    return this.headers$().pipe(
      switchMap(h => this.http.get<TenantResponse[]>(`${this.BASE}/tenants`, { headers: h }))
    );
  }

  createTenant(p: TenantPayload): Observable<TenantResponse> {
    return this.headers$().pipe(
      switchMap(h => this.http.post<TenantResponse>(`${this.BASE}/tenants`, p, { headers: h }))
    );
  }

  updateTenant(id: string, p: Partial<TenantPayload>): Observable<TenantResponse> {
    return this.headers$().pipe(
      switchMap(h => this.http.patch<TenantResponse>(`${this.BASE}/tenants/${id}`, p, { headers: h }))
    );
  }

  deleteTenant(id: string): Observable<void> {
    return this.headers$().pipe(
      switchMap(h => this.http.delete<void>(`${this.BASE}/tenants/${id}`, { headers: h }))
    );
  }

  // ── Admins ─────────────────────────────────────────────────────────────────
  listAdmins(tenantId: string): Observable<UserItem[]> {
    return this.headers$().pipe(
      switchMap(h =>
        this.http.get<UserItem[]>(`${this.BASE}/tenants/${tenantId}/admins`, { headers: h })
      )
    );
  }

  createAdmin(tenantId: string, p: AdminPayload): Observable<UserItem> {
    return this.headers$().pipe(
      switchMap(h =>
        this.http.post<UserItem>(`${this.BASE}/tenants/${tenantId}/admins`, p, { headers: h })
      )
    );
  }

  deleteAdmin(tenantId: string, userId: string): Observable<void> {
    return this.headers$().pipe(
      switchMap(h =>
        this.http.delete<void>(`${this.BASE}/tenants/${tenantId}/admins/${userId}`, { headers: h })
      )
    );
  }

  // ── All users ──────────────────────────────────────────────────────────────
  listAllUsers(): Observable<UserItem[]> {
    return this.headers$().pipe(
      switchMap(h => this.http.get<UserItem[]>(`${this.BASE}/users`, { headers: h }))
    );
  }
}