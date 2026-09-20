        %% --------水位流量关系曲线----------------------
        % 参数
        % ------------------------------
        n = A(jjnum).caolv;               % 曼宁糙率
        S = A(jjnum).bijiang;          % 坡降（‰ -> 换算为无量纲）
        A(jjnum).Hvec = (A(jjnum).Dmin:dH:A(jjnum).ZYmin)';  % 水位范围
        Hvec = A(jjnum).Hvec;
        
        
        % 预分配
        Qvec = zeros(size(Hvec));
        Avec = zeros(size(Hvec));
        Pvec = zeros(size(Hvec));
        Bvec = zeros(size(Hvec));  % 顶宽（水面宽度）
        
        
        % ------------------------------
        % 循环计算每个水位下的A, P, B, Q
        % ------------------------------
        for k = 1:length(Hvec)
            H = Hvec(k);
            As = 0;
            P = 0;
            x_left = inf;
            x_right = -inf;

            for j = 1:N-1
                x1 = x(j); z1 = z(j);
                x2 = x(j+1); z2 = z(j+1);

                % 若线段两端都在水位以下（或等于）
                if z1 <= H && z2 <= H
                    dx = x2 - x1;
                    % 面积（床面到水位的梯形）
                    As = As + ((H - z1) + (H - z2))/2 * dx;
                    % 湿周加床面长度
                    P = P + sqrt((x2 - x1)^2 + (z2 - z1)^2);
                    % 更新水面左右界（端点在水下）
                    x_left = min(x_left, x1);
                    x_right = max(x_right, x2);

                % 若线段跨越水位（有一端在水下，一端在水上）
                elseif (z1 <= H && z2 > H) || (z1 > H && z2 <= H)
                    t = (H - z1) / (z2 - z1);      % 线性插值参数
                    xi = x1 + t * (x2 - x1);       % 交点横坐标 (y = H)
                    % 记录交点为水面边界
                    x_left = min(x_left, xi);
                    x_right = max(x_right, xi);

                    if z1 <= H && z2 > H
                        % 湿润部分为 x1 -> xi
                        dx = xi - x1;
                        As = As + ((H - z1) + 0)/2 * dx;
                        P = P + sqrt((xi - x1)^2 + (H - z1)^2);
                    else
                        % z1 > H && z2 <= H 湿润部分为 xi -> x2
                        dx = x2 - xi;
                        As = As + (0 + (H - z2))/2 * dx;
                        P = P + sqrt((x2 - xi)^2 + (H - z2)^2);
                    end
                end
                % 其它情况（两端都高于水位）对当前段无贡献
            end

            % 如果没有湿润区域，则 Q=0
            if isinf(x_left) || isinf(x_right) || As <= 0 || P <= 0
                Avec(k) = 0;
                Pvec(k) = 0;
                Bvec(k) = 0;
                Qvec(k) = 0;
                continue;
            end

            % 顶宽
            B = x_right - x_left;
            R = As / P;  % 水力半径

            % 曼宁公式
            Q = (1 / n) * As * R^(2/3) * sqrt(S);

            % 存储
            Avec(k) = As;
            Pvec(k) = P;
            Bvec(k) = B;
            Qvec(k) = Q;
        end
        A(jjnum).Qvec = Qvec;
        A(jjnum).Avec = Avec;
        A(jjnum).Pvec = Pvec;
        A(jjnum).Bvec = Bvec;
