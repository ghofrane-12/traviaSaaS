// streaming-status/streaming-status.component.ts
import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface StreamingStep {
  id:       string;
  message:  string;
  state:    'pending' | 'done' | 'error';
  count?:   number;
  subType?: string;
}

@Component({
  selector: 'app-streaming-status',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './streaming-status.component.html',
  styleUrls: ['./streaming-status.component.scss']
})
export class StreamingStatusComponent {
  @Input() steps: StreamingStep[] = [];

  getIcon(step: StreamingStep): string {
    if (step.state === 'done')  return '✓';
    if (step.state === 'error') return '✕';
    return this.getTypeIcon(step.subType);
  }

  getTypeIcon(subType?: string): string {
    const icons: Record<string, string> = {
      flight:     '✈',
      hotel:      '🏨',
      restaurant: '🍽',
      transport:  '🚌',
      activity:   '🎭',
      specialty:  '🥘',
      info:       '💡',
      classifier: '🔍',
    };
    return icons[subType ?? 'info'] ?? '⋯';
  }

  getCountLabel(step: StreamingStep): string {
    if (step.count === undefined) return '';
    if (step.count === 0) return 'Aucun résultat';
    return `${step.count} résultat${step.count > 1 ? 's' : ''}`;
  }
}