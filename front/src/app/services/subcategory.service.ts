import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { SubcategoryConfig } from '../components/models/subcategory-config';
import { environment } from '../environments/environment';

@Injectable({ providedIn: 'root' })
export class SubcategoryService {
    private apiUrl = environment.apiUrl;

  constructor(private http: HttpClient) {}

getConfigs(tenantId: string) { return this.http.get<SubcategoryConfig[]>(`${this.apiUrl}tenants/${tenantId}/subcategories/`); }
  create(payload: any) { return this.http.post(`${this.apiUrl}tenants/${payload.tenant_id}/subcategories/`, payload); }
  update(tenantId: string, configId: string, payload: any) { return this.http.patch(`${this.apiUrl}tenants/${tenantId}/subcategories/${configId}`, payload); }
  delete(tenantId: string, configId: string) { return this.http.delete(`${this.apiUrl}tenants/${tenantId}/subcategories/${configId}`); }
}