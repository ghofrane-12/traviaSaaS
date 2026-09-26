import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { Categories } from '../components/models/category';
import { environment } from '../environments/environment';

@Injectable({ providedIn: 'root' })
export class CategoryService {
  private apiUrl = environment.apiUrl;
  constructor(private http: HttpClient) {}

    getCategories() { return this.http.get<Categories>(`${this.apiUrl}config/categories`); }

}