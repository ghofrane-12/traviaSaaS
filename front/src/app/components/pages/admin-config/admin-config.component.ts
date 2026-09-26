import { Component, OnInit, ChangeDetectorRef, HostBinding,inject } from '@angular/core';
import { CommonModule, NgIf, NgFor } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { CategoryService } from '../../../services/category.service';
import { SubcategoryService } from '../../../services/subcategory.service';
import { FileService } from '../../../services/file.service';
import { ApiKeyService } from '../../../services/api-key.service';
import { Categories } from '../../models/category';
import {
  SubcategoryConfig, ResponseType, TenantFile,
  Provider, ApiKeyOut, ApiKeyCreate,
  ParamMappingEntry
} from '../../models/subcategory-config';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { UserService } from '../../../services/user.service';
import { environment } from '../../../environments/environment';
import { AgentService } from '../../../services/agent.service';
import { OfferService, OfferMappingResponse, OfferMappingCreate } from '../../../services/offer.service';
interface ApiFormState {
  label: string;
  provider_id: string;
  agent_type: string;
  api_url: string;
  api_key: string;
  http_method: string;
  result_path: string;
  priority: number;
  timeout_ms: number;
  retry_count: number;
  headers_template: string;
  payload_template: string;
  paramMappingRows: ParamMappingEntry[];
  saving: boolean;
  error: string;
  saved: boolean;
}
interface FieldMapRow {
  standardField: string;
  sourceField:   string;
}

interface SubcategoryState {
  name: string;
  enabled: boolean;
  responseType: ResponseType | null;
  config?: SubcategoryConfig;
  showFilePanel: boolean;
  touched: boolean;
  llmPrompt: string;
  humanContact: string;
  selectedFileIds: Set<string>;
  apiForm: ApiFormState;
  existingApiKey?: ApiKeyOut;
  linkMap: Map<string, string>
}

interface CategoryState {
  name: string;
  expanded: boolean;
  subcategories: SubcategoryState[];
}

type ActiveView = 'subcategories' | 'files' | 'offers' | 'reviews' | 'tickets' | 'messenger';
function defaultApiForm(): ApiFormState {
  return {
    label: '',
    provider_id: '',
    agent_type: '',
    api_url: '',
    api_key: '',
    http_method: 'POST',
    result_path: '',
    priority: 1,
    timeout_ms: 5000,
    retry_count: 1,
    headers_template: '',
    payload_template: '',
    paramMappingRows: [],
    saving: false,
    error: '',
    saved: false
  };
}

@Component({
  selector: 'app-admin-config',
  standalone: true,
  imports: [CommonModule, FormsModule, NgIf, NgFor],
  templateUrl: './admin-config.component.html',
  styleUrls: ['./admin-config.component.scss']
})
export class AdminConfigComponent implements OnInit {
  readonly subcategoryAgentTypeMap: Record<string, string> = {
    'Luggage & Safety': 'compliance',
    'Special Accessibility Needs': 'compliance',
    'Vaccination & Health Requirements': 'compliance',
    'Visa & Passport': 'compliance',
    'Weather & Best Seasons': 'info',
    'Internet & Connectivity': 'info',
    'Cultural Norms': 'info',
    'Gastronomy': 'discovery',
    'Car Rental Booking': 'transport',
    'Flight Booking': 'transport',
    'Transportation Search': 'transport',
    'Car Rental Management': 'transport',
    'Flight Management': 'transport',
    'Restaurant Reservation': 'discovery',
    'Event Ticket Booking': 'activity',
    'Tour/Excursion Booking': 'activity',
    'Tour/Excursion Management': 'activity',
    'Hotel Booking': 'stay',
    'Hotel Management': 'stay',
    'Emergency Contacts': 'security',
    'Fraud Prevention': 'security',
    'Lost Or Stolen Items': 'security',
    'Insurance & Refund Policies': 'security',
    'Flight Disruption': 'claims',
    'Overbooking': 'claims',
    'Baggage Damage': 'claims',
    'Complaint Submission': 'claims',
    'Airport Logistics': 'logistics',
    'Check-In': 'logistics',
    'Late Check-Out': 'logistics',
    'Child Services': 'assistance',
    'Pet Policy': 'assistance',
    'Agency Details & Contact': 'general',
    'Chatbot Capabilities':     'general',
    'Human Handoff':            'general',
    'Feedback & Reviews':       'general',
  };
  tenantId: string = '';
  agencyName = '';
  agencyLogo = '';
  tone = 'formal';
  activeView: ActiveView = 'subcategories';
  categories: CategoryState[] = [];
  files: TenantFile[] = [];
  providers: Provider[] = [];
  apiKeys: ApiKeyOut[] = [];
  saving = false;
  savedMessage = false;
  sidebarOpen = false;
  facebookConnected = false;
facebookPageName  = '';

reviews: any[] = [];
reviewsLoading = false;
reviewsAvg = 0;

tickets: any[] = [];
ticketsLoading = false;
  uploadName = '';
  uploadAgentType = '';
  uploadFile: File | null = null;
  uploading = false;
  uploadError = '';
  uploadSuccess = false;
  @HostBinding('attr.data-tone') get dataTone() { return this.tone; }
  mappings: OfferMappingResponse[] = [];
  mappingForm = {
    offer_type:     'hotel',
    fieldMapRows:   [] as FieldMapRow[],
    defaultCurrency: 'TND',
    defaultAvailable: true,
    saving:  false,
    saved:   false,
    error:   ''
  };
  readonly offerTypes = ['hotel', 'flight', 'circuit', 'omra'];
readonly standardFields = [
  'title', 'destination', 'origin', 'price',
  'currency', 'departure_date', 'duration_days',
  'image_url', 'description', 'is_available'
];
 readonly agentTypes = [
  'transport', 'stay', 'activity', 'discovery',
  'info', 'compliance', 'logistics', 'claims',
  'security', 'assistance',
  'hotel', 'flight', 'circuit', 'omra',
  'general'
];

  readonly httpMethods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
  private agentService = inject(AgentService);
    private router       = inject(Router);


  constructor(
    private categoryService: CategoryService,
    private subcategoryService: SubcategoryService,
    private fileService: FileService,
    private apiKeyService: ApiKeyService,
    private cdr: ChangeDetectorRef,
    private userService: UserService,
    private offerService:OfferService,
    private http: HttpClient
  ) {}

  ngOnInit(): void {
     const tid = this.userService.tenantId();
      if (!tid) return;
      this.tenantId = tid;
      this.agencyName = this.userService.agencyName();
      this.agencyLogo = this.userService.agencyLogo() ?? '';
      this.tone = this.userService.tone();
console.log('agencyName:', this.agencyName, 'tone:', this.tone, 'agencyLogo:', this.agencyLogo);

    this.apiKeyService.getProviders().subscribe((p: Provider[]) => {
      this.providers = p;
      this.cdr.detectChanges();
    });
    this.loadAll();
     this.loadMappings();
    this.checkFacebookStatus();

  }
checkFacebookStatus(): void {
  this.http.get<any>(`${environment.apiUrl}admin/tenants/${this.tenantId}/facebook/status`)
    .subscribe(status => {
      this.facebookConnected = status.ready;
      this.cdr.detectChanges();
    });
}
connectFacebook(): void {
  window.location.href = `${environment.backendUrl}/facebook/connect/${this.tenantId}`;
}
   
loadMappings(): void {
  const tid = this.userService.tenantId();
  console.log('[MAPPINGS] tenant_id:', tid);
  console.log('[MAPPINGS] role:', this.userService.role());

  this.offerService.getMappings().subscribe({
    next: (mappings) => {
      console.log('[MAPPINGS] résultat:', mappings);
      this.mappings = mappings;
      this.cdr.detectChanges();
    },
    error: (err) => {
      console.error('[MAPPINGS] erreur:', err.status, err.error);
    }
  });
}

onOfferTypeChange(): void {
  const existing = this.mappings.find(m => m.offer_type === this.mappingForm.offer_type);
  if (existing) {
    this.mappingForm.fieldMapRows = Object.entries(existing.field_map).map(
      ([standardField, sourceField]) => ({ standardField, sourceField })
    );
    this.mappingForm.defaultCurrency   = existing.default_values['currency']     ?? 'TND';
    this.mappingForm.defaultAvailable  = existing.default_values['is_available'] ?? true;
  } else {
    this.mappingForm.fieldMapRows = [];
  }
  this.cdr.detectChanges();
}

addFieldMapRow(): void {
  this.mappingForm.fieldMapRows.push({
    standardField: 'title',
    sourceField:   ''
  });
}

removeFieldMapRow(index: number): void {
  this.mappingForm.fieldMapRows.splice(index, 1);
}

saveMapping(): void {
  this.mappingForm.error = '';

  if (!this.mappingForm.offer_type) {
    this.mappingForm.error = 'Choisissez un type d\'offre.';
    return;
  }
  if (this.mappingForm.fieldMapRows.length === 0) {
    this.mappingForm.error = 'Ajoutez au moins un champ.';
    return;
  }
  const emptyRow = this.mappingForm.fieldMapRows.find(r => !r.sourceField.trim());
  if (emptyRow) {
    this.mappingForm.error = 'Tous les champs source doivent être remplis.';
    return;
  }

  const field_map: Record<string, string> = {};
  this.mappingForm.fieldMapRows.forEach(row => {
    field_map[row.standardField] = row.sourceField.trim();
  });

  const payload: OfferMappingCreate = {
    offer_type:     this.mappingForm.offer_type,
    field_map,
    default_values: {
      currency:     this.mappingForm.defaultCurrency,
      is_available: this.mappingForm.defaultAvailable
    }
  };

  this.mappingForm.saving = true;
  this.offerService.saveMapping(payload).subscribe({
    next: (saved) => {
      this.mappingForm.saving = false;
      this.mappingForm.saved  = true;
      const idx = this.mappings.findIndex(m => m.offer_type === saved.offer_type);
      if (idx >= 0) this.mappings[idx] = saved;
      else this.mappings.push(saved);
      setTimeout(() => this.mappingForm.saved = false, 3000);
      this.cdr.detectChanges();
    },
    error: () => {
      this.mappingForm.saving = false;
      this.mappingForm.error  = 'Erreur lors de la sauvegarde.';
      this.cdr.detectChanges();
    }
  });
}

getMappingStatus(offerType: string): boolean {
  return this.mappings.some(m => m.offer_type === offerType);
}
loadAll(): void {
  this.apiKeyService.getApiKeys(this.tenantId).subscribe((keys: ApiKeyOut[]) => {
    this.apiKeys = keys;

    this.categoryService.getCategories().subscribe((cats: Categories) => {
      this.subcategoryService.getConfigs(this.tenantId).subscribe((configs: SubcategoryConfig[]) => {
        this.categories = Object.entries(cats).map(([catName, subcats], i) => ({
          name: catName,
          expanded: i === 0,
          subcategories: subcats.map((sub: string) => {
            const existing = configs.find((c: SubcategoryConfig) => c.sub_category === sub);
            const existingKey = keys.find((k: ApiKeyOut) => k.label === sub);
            const form = defaultApiForm();
            if (existingKey) {
              form.label = existingKey.label;
              form.provider_id = existingKey.provider_id;
              form.agent_type = existingKey.agent_type;
              form.api_url = existingKey.api_url;
              form.http_method = existingKey.http_method;
              form.result_path = existingKey.result_path ?? '';
              form.priority = existingKey.priority;
              form.timeout_ms = existingKey.timeout_ms;
              form.retry_count = existingKey.retry_count;
              form.headers_template = existingKey.headers_template
                ? JSON.stringify(existingKey.headers_template, null, 2) : '';
              form.payload_template = existingKey.payload_template
                ? JSON.stringify(existingKey.payload_template, null, 2) : '';
              form.paramMappingRows = this.parseParamMapping(existingKey.param_mapping);
            }
            return {
              name: sub,
              enabled: !!existing?.is_active,
              responseType: existing?.response_type ?? null,
              config: existing,
              showFilePanel: existing?.response_type === 'rag',
              touched: false,
              llmPrompt: existing?.llm_prompt_override ?? '',
              humanContact: existing?.human_contact ?? '',
              selectedFileIds: new Set<string>(),
              linkMap: new Map<string, string>(),
              apiForm: form,
              existingApiKey: existingKey
            };
          })
        }));

        this.categories.forEach(cat => {
          cat.subcategories.forEach(sub => {
            if (sub.config && sub.responseType === 'rag') {
              this.fileService.getLinksForSubcategory(this.tenantId, sub.config.id)
                .subscribe((links: any[]) => {
                  links.forEach(link => {
                    sub.selectedFileIds.add(link.file_id);
                    sub.linkMap.set(link.file_id, link.id);
                  });
                  this.cdr.detectChanges();
                });
            }
          });
        });

        this.cdr.detectChanges();
      });
    });
  });

  this.fileService.getFiles(this.tenantId).subscribe((f: TenantFile[]) => {
    this.files = f;
    this.cdr.detectChanges();
  });
}

  setView(view: ActiveView): void {
  this.activeView = view;
  if (view === 'reviews') this.loadReviews();
  if (view === 'tickets') this.loadTickets();
}
loadReviews(): void {
  this.reviewsLoading = true;
  this.http.get<any>(`${environment.apiUrl}general/reviews/${this.tenantId}?limit=50`)
    .subscribe({
      next: (data) => {
        this.reviews    = data.reviews || [];
        this.reviewsAvg = data.avg_rating || 0;
        this.reviewsLoading = false;
        this.cdr.detectChanges();
      },
      error: () => { this.reviewsLoading = false; }
    });
}
getTotalSubcategories(): number {
  return this.categories.reduce((acc, cat) => 
    acc + cat.subcategories.length, 0);
}

loadTickets(): void {
  this.ticketsLoading = true;
  this.http.get<any[]>(`${environment.apiUrl}general/tickets/${this.tenantId}`)
    .subscribe({
      next: (data) => {
        this.tickets = data;
        this.ticketsLoading = false;
        this.cdr.detectChanges();
      },
      error: () => { this.ticketsLoading = false; }
    });
}
updateTicketStatus(ticketId: string, status: string): void {
  this.http.patch(`${environment.apiUrl}general/tickets/${ticketId}`, { status })
    .subscribe(() => {
      const t = this.tickets.find(t => t.ticket_id === ticketId);
      if (t) t.status = status;
      this.cdr.detectChanges();
    });
}
  goToChat() {
    const sessionId = this.agentService.getSessionId();
    this.router.navigate(['/chat'], {
      queryParams: sessionId ? { session_id: sessionId } : {}
    });
  }
  toggleCategory(cat: CategoryState): void {
    cat.expanded = !cat.expanded;
  }

  toggleSubcategory(sub: SubcategoryState): void {
    sub.enabled = !sub.enabled;
    sub.touched = true;
    if (!sub.enabled) {
      sub.responseType = null;
      sub.showFilePanel = false;
    }
  }

  setResponseType(sub: SubcategoryState, type: ResponseType): void {
    sub.responseType = type;
    sub.touched = true;
    sub.showFilePanel = type === 'rag';
  }

  hasError(sub: SubcategoryState): boolean {
    return sub.enabled && sub.responseType === null && sub.touched;
  }
toggleFileSelection(sub: SubcategoryState, fileId: string): void {
  if (!sub.config) {
    alert('Sauvegardez d\'abord la configuration de la sous-catégorie.');
    return;
  }

  if (sub.selectedFileIds.has(fileId)) {
    const linkId = sub.linkMap.get(fileId);
    if (!linkId) return;

    this.fileService.unlinkFile(this.tenantId, linkId).subscribe({
      next: () => {
        sub.selectedFileIds.delete(fileId);
        sub.linkMap.delete(fileId);
        this.cdr.detectChanges();
      },
      error: () => console.error('Erreur suppression lien')
    });
  } else {
    this.fileService.linkFile(this.tenantId, fileId, sub.config.id).subscribe({
      next: (link: any) => {
        sub.selectedFileIds.add(fileId);
        sub.linkMap.set(fileId, link.id);
        this.cdr.detectChanges();
      },
      error: () => console.error('Erreur liaison fichier')
    });
  }
}

  isFileSelected(sub: SubcategoryState, fileId: string): boolean {
    return sub.selectedFileIds.has(fileId);
  }

  getEnabledCount(cat: CategoryState): number {
    return cat.subcategories.filter(s => s.enabled).length;
  }

  getTotalEnabled(): number {
    return this.categories.reduce((acc, cat) =>
      acc + cat.subcategories.filter(s => s.enabled).length, 0);
  }

saveApiKey(sub: SubcategoryState): void {
  const f = sub.apiForm;
  f.error = '';

  if (!f.label.trim()) { f.error = 'Le label est obligatoire.'; return; }
  if (!f.provider_id) { f.error = 'Sélectionnez un provider.'; return; }
  if (!f.api_url.trim()) { f.error = "L'URL API est obligatoire."; return; }
  if (!f.api_key.trim()) { f.error = 'La clé API est obligatoire.'; return; }

  let headers: any = undefined;
  let payload: any = undefined;
  try {
    if (f.headers_template.trim()) headers = JSON.parse(f.headers_template);
    if (f.payload_template.trim()) payload = JSON.parse(f.payload_template);
  } catch {
    f.error = 'Les champs JSON (headers, payload) sont invalides.';
    return;
  }

  const mapping = this.buildParamMapping(f.paramMappingRows);
  const apiPayload: ApiKeyCreate = {
    label:            f.label,
    provider_id:      f.provider_id,
    agent_type:       this.subcategoryAgentTypeMap[sub.name] ?? sub.name,
    api_url:          f.api_url,
    api_key:          f.api_key,
    http_method:      f.http_method,
    result_path:      f.result_path || undefined,
    priority:         f.priority,
    timeout_ms:       f.timeout_ms,
    retry_count:      f.retry_count,
    headers_template: headers,
    payload_template: payload,
    param_mapping:    mapping
  };

  f.saving = true;

  // ← PATCH si clé existante, POST sinon
  const call$ = sub.existingApiKey
    ? this.apiKeyService.updateApiKey(this.tenantId, sub.existingApiKey.key_id, apiPayload)
    : this.apiKeyService.createApiKey(this.tenantId, apiPayload);

  call$.subscribe({
    next: (key: ApiKeyOut) => {
      f.saving = false;
      f.saved  = true;
      sub.existingApiKey = key;
      setTimeout(() => f.saved = false, 3000);
      this.cdr.detectChanges();
    },
    error: () => {
      f.saving = false;
      f.error  = "Erreur lors de la sauvegarde.";
      this.cdr.detectChanges();
    }
  });
}

  deleteApiKey(sub: SubcategoryState): void {
    if (!sub.existingApiKey) return;
    if (!confirm('Supprimer cette clé API ?')) return;
    this.apiKeyService.deleteApiKey(this.tenantId, sub.existingApiKey.key_id).subscribe(() => {
      sub.existingApiKey = undefined;
      sub.apiForm = defaultApiForm();
      this.cdr.detectChanges();
    });
  }

  saveAll(): void {
    let hasErrors = false;
    this.categories.forEach(cat => {
      cat.subcategories.forEach(sub => {
        if (sub.enabled) {
          sub.touched = true;
          if (!sub.responseType) hasErrors = true;
        }
      });
    });
    if (hasErrors) return;

    this.saving = true;
    const calls: Promise<any>[] = [];

    this.categories.forEach(cat => {
      cat.subcategories.forEach(sub => {
        const payload = {
          is_active: sub.enabled,
          response_type: sub.responseType ?? undefined,
          llm_prompt_override: sub.llmPrompt || undefined,
          human_contact: sub.humanContact || undefined
        };
        if (sub.enabled && !sub.config) {
            calls.push(
              this.subcategoryService.create({
                tenant_id: this.tenantId,
                sub_category: sub.name,
                is_active: true,
                response_type: sub.responseType!,
                agent_type: this.subcategoryAgentTypeMap[sub.name] ?? null, 
                llm_prompt_override: sub.llmPrompt || undefined,
                human_contact: sub.humanContact || undefined
              }).toPromise()
            );
        } else if (sub.config) {
          calls.push(
            this.subcategoryService.update(this.tenantId, sub.config.id, payload).toPromise()
          );
        }
      });
    });

    Promise.all(calls).then(() => {
      this.saving = false;
      this.savedMessage = true;
      setTimeout(() => this.savedMessage = false, 3000);
      this.loadAll();
    });
  }
getFilesForSubcategory(sub: SubcategoryState): TenantFile[] {
  return this.files.filter(f => f.embedding_status !== 'failed');
}
  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files?.length) {
      this.uploadFile = input.files[0];
      if (!this.uploadName) {
        this.uploadName = input.files[0].name.replace(/\.[^/.]+$/, '');
      }
    }
  }

  uploadFileSubmit(): void {
    this.uploadError = '';
    if (!this.uploadName.trim()) { this.uploadError = 'Le nom est obligatoire.'; return; }
    if (!this.uploadAgentType) { this.uploadError = "Le type d'agent est obligatoire."; return; }
    if (!this.uploadFile) { this.uploadError = 'Sélectionnez un fichier.'; return; }

    this.uploading = true;
    this.fileService.uploadFile(
      this.tenantId, this.uploadName, this.uploadAgentType, this.uploadFile
    ).subscribe({
      next: () => {
        this.uploading = false;
        this.uploadSuccess = true;
        this.uploadName = '';
        this.uploadAgentType = '';
        this.uploadFile = null;
        setTimeout(() => this.uploadSuccess = false, 3000);
        this.fileService.getFiles(this.tenantId).subscribe((f: TenantFile[]) => {
          this.files = f;
          this.cdr.detectChanges();
        });
      },
      error: () => {
        this.uploading = false;
        this.uploadError = "Erreur lors de l'upload.";
      }
    });
  }

  deleteFile(fileId: string): void {
    if (!confirm('Supprimer ce fichier ?')) return;
    this.fileService.deleteFile(this.tenantId, fileId).subscribe(() => {
      this.files = this.files.filter(f => f.file_id !== fileId);
      this.cdr.detectChanges();
    });
  }

  responseTypeLabel(type: ResponseType): string {
    const labels: Record<ResponseType, string> = {
      api: 'API', rag: 'RAG', llm: 'LLM', human: 'Human'
    };
    return labels[type];
  }

  formatSize(bytes: number): string {
    if (!bytes) return '—';
    if (bytes < 1024) return `${bytes} o`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
  }

  readonly entityTypes = ['LOC', 'DATE', 'PRICE', 'MISC', 'FLIGHT', 'ORG', 'PERSONS'];
  readonly entityIndexes = [0, 1, 2, 3, 4];

addParamRow(sub: SubcategoryState): void {
  sub.apiForm.paramMappingRows.push({
    entityType: 'LOC',
    entityIndex: 0,
    param: '',
    transform: '',
    default: '',
    optional: false
  });
}

removeParamRow(sub: SubcategoryState, index: number): void {
  sub.apiForm.paramMappingRows.splice(index, 1);
}

buildParamMapping(rows: ParamMappingEntry[]): any {
  const result: any = {};
  rows.forEach(row => {
    if (!row.entityType || !row.param) return;
    const key = `${row.entityType}[${row.entityIndex}]`;
    const entry: any = { param: row.param };
    if (row.transform) entry.transform = row.transform;
    if (row.default) entry.default = row.default;
    entry.optional = row.optional;
    result[key] = entry;
  });
  return result;
}

parseParamMapping(mapping: any): ParamMappingEntry[] {
  if (!mapping) return [];
  return Object.entries(mapping).map(([key, val]: [string, any]) => {
    const match = key.match(/^([A-Z]+)\[(\d+)\]$/);
    return {
      entityType: match ? match[1] : 'LOC',
      entityIndex: match ? parseInt(match[2]) : 0,
      param: val.param ?? '',
      transform: val.transform ?? '',
      default: val.default ?? '',
      optional: val.optional ?? false
    };
  });
}
}