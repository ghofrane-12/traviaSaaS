import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { environment } from '../environments/environment';
import { firstValueFrom } from 'rxjs';
import { UserService } from './user.service';

@Injectable({ providedIn: 'root' })
export class ResultsCacheService {

  constructor(
    private http:        HttpClient,
    private userService: UserService,
  ) {}

  private async headers(): Promise<{ headers: HttpHeaders }> {
    const token = await this.userService.getFirebaseToken();
    return {
      headers: new HttpHeaders({
        'Content-Type':  'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      })
    };
  }

async saveResults(sessionId: string, segments: any[]): Promise<void> {
  if (!segments.length) return;
  
  sessionStorage.setItem(`results_${sessionId}`, JSON.stringify(segments));
  
  try {
    const opts = await this.headers();
    const result: any = await firstValueFrom(
      this.http.post(
        `${environment.apiUrl}results/save`,
        { session_id: sessionId, segments },
        opts
      )
    );
    console.log('[ResultsCache] ✅ Résultats sauvegardés | source:', result?.source);
  } catch (err) {
    console.warn('[ResultsCache] ⚠ Erreur sauvegarde:', err);
  }
}
async clearCache(sessionId: string): Promise<void> {
  sessionStorage.removeItem(`results_${sessionId}`);
  
  try {
    const opts = await this.headers();
    await firstValueFrom(
      this.http.delete(
        `${environment.apiUrl}results/clear/${sessionId}`,
        opts
      )
    );
  } catch (err) {
    console.warn('[ResultsCache] ⚠ Erreur clear cache:', err);
  }
}

async getLastResults(sessionId: string): Promise<any[]> {
  if (!sessionId) return [];
  
  try {
    const opts = await this.headers();
    console.log('[ResultsCache] 🔍 GET', `${environment.apiUrl}results/last/${sessionId}`);
    const res: any = await firstValueFrom(
      this.http.get(
        `${environment.apiUrl}results/last/${sessionId}`,
        opts
      )
    );
    console.log('[ResultsCache] réponse:', res);
    if (res?.found && res.segments?.length) {
      return res.segments;
    }
  } catch (err) {
    console.warn('[ResultsCache] ⚠ Erreur récupération:', err);
  }

  const local = sessionStorage.getItem(`results_${sessionId}`);
  if (local) {
    console.log('[ResultsCache] ✅ Restauré depuis sessionStorage');
    return JSON.parse(local);
  }

  return [];
}
}