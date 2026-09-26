import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { TenantFile } from '../components/models/subcategory-config';
import { environment } from '../environments/environment';

@Injectable({ providedIn: 'root' })
export class FileService {
  private apiUrl = environment.apiUrl;

  constructor(private http: HttpClient) {}

  getFiles(tenantId: string): Observable<TenantFile[]> {
    return this.http.get<TenantFile[]>(`${this.apiUrl}tenants/${tenantId}/files`);
  }

  uploadFile(tenantId: string, name: string, agentType: string, file: File): Observable<TenantFile> {
    const formData = new FormData();
    formData.append('name', name);
    formData.append('agent_type', agentType);
    formData.append('file', file);
    return this.http.post<TenantFile>(`${this.apiUrl}tenants/${tenantId}/files`, formData);
  }

  deleteFile(tenantId: string, fileId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}tenants/${tenantId}/files/${fileId}`);
  }

  linkFile(tenantId: string, fileId: string, subcategoryId: string): Observable<any> {
    return this.http.post(`${this.apiUrl}tenants/${tenantId}/file-subcategory`, {
      file_id: fileId,
      subcategory_id: subcategoryId,
      priority: 0,
      is_active: true
    });
  }

  getLinksForSubcategory(tenantId: string, subcategoryId: string): Observable<any[]> {
    return this.http.get<any[]>(
      `${this.apiUrl}tenants/${tenantId}/subcategories/${subcategoryId}/links`
    );
  }

  unlinkFile(tenantId: string, linkId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}tenants/${tenantId}/file-subcategory/${linkId}`);
  }
}