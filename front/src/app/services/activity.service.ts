import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../environments/environment';

export interface ActivityDetails {
  id: string;
  name: string;
  type: string;
  locale: string;
  url: string;
  description: string;
  additionalInfo: string;
  description_long?: string;
  info: string;
  pleaseNote: string;
  images: any[];
  source?: string;

  duration?: string;
  highlights?: string[];
  whats_included?: string[];
  whats_not_included?: string[];
  meeting_point?: string;
  cancellation_policy?: string;
  accessibility_info?: string;
  languages?: string[];
  artists?: string[];
  category?: string;

  dates?: {
    start: {
      localDate: string;
      localTime: string;
      dateTime: string;
      dateTBD: boolean;
      dateTBA: boolean;
      timeTBA: boolean;
      noSpecificTime: boolean;
    };
    timezone: string;
    status?: { code: string };
    spanMultipleDays: boolean;
  } | null;

  priceRanges: Array<{
    type: string;
    currency: string;
    min: number;
    max: number;
  }>;

  venue: {
    name: string;
    address: string;
    city: string;
    state: string;
    country: string;
    postalCode: string;
    location: { longitude: string; latitude: string };
  };

  externalLinks?: {
    facebook?: { url: string }[];
    twitter?:  { url: string }[];
    instagram?: { url: string }[];
    youtube?:  { url: string }[];
    homepage?: { url: string }[];
    wiki?:     { url: string }[];
  };

  upcomingEvents?: { total: number; };
  promoter?: any;
  seatmap?: any;
  status: string;
}

@Injectable({ providedIn: 'root' })
export class ActivityService {
  private baseUrl = environment.apiUrl;

  constructor(private http: HttpClient) {}

  getActivityDetails(eventId: string, subCategory: string = ''): Observable<ActivityDetails> {
    let url = `${this.baseUrl}chat/activity/${eventId}`;
    
    if (subCategory) {
      url += `?sub_category=${encodeURIComponent(subCategory)}`;
    }
    
    console.log('[ActivityService] Appel API:', url);
    
    return this.http.get<ActivityDetails>(url).pipe(
      catchError(this.handleError)
    );
  }

  private handleError(error: any): Observable<never> {
    console.error('[ActivityService] Erreur:', error);
    return throwError(() => error);
  }
}