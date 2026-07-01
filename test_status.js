// Тестовый скрипт для проверки логики отображения статуса
import { AWGManager } from './src/awgManager.js';

async function test() {
  const awgManager = new AWGManager();
  await awgManager.initialize();
  
  // Проверяем статус контейнера v1
  const container = awgManager.availableContainers.find(c => c.version === 'v1');
  if (!container) {
    console.log('❌ Контейнер v1 не найден');
    return;
  }
  
  console.log('📦 Контейнер:', container.name);
  
  // Проверяем статус
  const containerStatus = await awgManager.checkContainer(container.name);
  console.log('🔍 Статус контейнера:', JSON.stringify(containerStatus, null, 2));
  
  // Проверяем интерфейс
  const { exec } = await import('child_process');
  const { promisify } = await import('util');
  const execAsync = promisify(exec);
  
  const configFile = 'wg0';
  try {
    const { stdout } = await execAsync(`docker exec ${container.name} wg show ${configFile} 2>&1`);
    console.log('✅ Интерфейс работает');
    console.log('Вывод wg show:', stdout.substring(0, 200));
  } catch (error) {
    console.log('❌ Ошибка интерфейса:', error.message);
  }
  
  // Получаем клиентов
  const clients = await awgManager.getClients(container.name);
  console.log('👥 Клиенты:', clients);
}

test().catch(console.error);

// Made with Bob
