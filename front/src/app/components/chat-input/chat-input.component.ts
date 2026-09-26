import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
@Component({
  selector: 'app-chat-input',
  templateUrl: './chat-input.component.html',
  standalone: true,
  styleUrls: ['./chat-input.component.scss'],
  imports: [FormsModule , CommonModule]
})
export class InputComponent {

  inputText = '';
  @Input() sidebarCollapsed = false;
  @Output() send = new EventEmitter<string>();

  onSend() {
    if(this.inputText.trim()) {
      this.send.emit(this.inputText.trim());
      this.inputText = '';
    }
  }

  onKeyPress(event: KeyboardEvent) {
    if (event.key === 'Enter') {
      this.onSend();
      event.preventDefault();
    }
  }

}