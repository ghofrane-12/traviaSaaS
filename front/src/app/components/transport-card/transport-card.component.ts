// transport-card.component.ts
import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { TransportResult } from '../../services/result.service';

@Component({
  selector: 'app-transport-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './transport-card.component.html',
  styleUrls: ['./transport-card.component.scss']

})
export class TransportCardComponent {
  @Input() transport!: TransportResult;
  @Output() viewDetails = new EventEmitter<string>();

  constructor(private router: Router) {}


}