import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { ApiKeyOut, ApiKeyCreate, Provider } from '../components/models/subcategory-config';

import { environment } from '../environments/environment';

@Injectable({ providedIn: 'root' })
export class ApiKeyService {
  private apiUrl = environment.apiUrl;
  constructor(private http:HttpClient){}

  getProviders() { return this.http.get<Provider[]>(`${this.apiUrl}providers/`); }
  getApiKeys(tenantId: string) { return this.http.get<ApiKeyOut[]>(`${this.apiUrl}tenants/${tenantId}/api-keys/`); }
  createApiKey(tenantId: string, payload: ApiKeyCreate) { return this.http.post<ApiKeyOut>(`${this.apiUrl}tenants/${tenantId}/api-keys/`, payload); }
  deleteApiKey(tenantId: string, keyId: string) { return this.http.delete<void>(`${this.apiUrl}tenants/${tenantId}/api-keys/${keyId}`); }
  updateApiKey(tenantId: string, keyId: string, payload: ApiKeyCreate) {
  return this.http.patch<ApiKeyOut>(
    `${this.apiUrl}tenants/${tenantId}/api-keys/${keyId}`,
    payload
  );
}
}